#!/usr/bin/env python3
"""Generate deterministic Phase 3 package, fixture and checksum manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE = "phase-03-lookups-and-offer-snapshots"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_entry(path: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def build_lookup_manifest() -> dict[str, object]:
    package = ROOT / "reservation_lookup"
    paths = sorted(package.glob("*.py"), key=lambda item: item.name)
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "package": "reservation_lookup",
        "python_file_count": len(paths),
        "files": [_file_entry(path) for path in paths],
    }


def build_fixture_manifest() -> dict[str, object]:
    base = ROOT / "tests" / "fixtures" / "phase3"
    paths = sorted(base.glob("*/*.json"), key=lambda item: str(item.relative_to(ROOT)))
    providers: dict[str, int] = {}
    for path in paths:
        provider = path.parent.name
        providers[provider] = providers.get(provider, 0) + 1
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "synthetic_sanitized_only": True,
        "fixture_count": len(paths),
        "provider_counts": dict(sorted(providers.items())),
        "files": [_file_entry(path) for path in paths],
    }


def checksum_paths() -> tuple[Path, ...]:
    relative = (
        ".github/workflows/phase3.yml",
        "reservation_lookup/README.md",
        "reservation_lookup/__init__.py",
        "reservation_lookup/_common.py",
        "reservation_lookup/types.py",
        "reservation_lookup/identity.py",
        "reservation_lookup/cloudbeds.py",
        "reservation_lookup/bokun.py",
        "reservation_lookup/selection.py",
        "reservation_lookup/properties.py",
        "scripts/run_phase3_properties.py",
        "scripts/run_phase3_mutations.py",
        "scripts/generate_phase3_manifest.py",
        "scripts/validate_phase3.py",
        "tests/test_phase3_lookup_types.py",
        "tests/test_phase3_cloudbeds_adapter.py",
        "tests/test_phase3_bokun_adapter.py",
        "tests/test_phase3_selection.py",
        "tests/test_phase3_properties.py",
        "tests/test_phase3_mutation_runner.py",
        "docs/refactor/phases/phase-03-lookups-and-offer-snapshots.md",
        "docs/refactor/evidence/phase-03/entry-baseline.json",
        "docs/refactor/evidence/phase-03/red-result-types.json",
        "docs/refactor/evidence/phase-03/red-result-cloudbeds.json",
        "docs/refactor/evidence/phase-03/red-result-bokun.json",
        "docs/refactor/evidence/phase-03/red-result-selection.json",
        "docs/refactor/evidence/phase-03/red-result-properties.json",
        "docs/refactor/evidence/phase-03/red-result-late-review.json",
        "docs/refactor/evidence/phase-03/property-result.json",
        "docs/refactor/evidence/phase-03/mutation-result.json",
        "docs/refactor/evidence/phase-03/performance-result.json",
        "docs/refactor/evidence/phase-03/source-map.json",
        "docs/refactor/evidence/phase-03/lookup-manifest.json",
        "docs/refactor/evidence/phase-03/fixture-manifest.json",
        "docs/refactor/evidence/phase-03/adversarial-review.md",
        "docs/refactor/evidence/phase-03/README.md",
        "docs/superpowers/specs/2026-07-18-phase-3-lookup-adapters-design.md",
        "docs/superpowers/plans/2026-07-18-phase-3-lookup-adapters.md",
    )
    return tuple(ROOT / item for item in relative)


def render_sums() -> str:
    missing = [str(path.relative_to(ROOT)) for path in checksum_paths() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing checksum targets: {missing}")
    return "".join(
        f"{sha256(path)}  {path.relative_to(ROOT)}\n" for path in checksum_paths()
    )


def _write_json(path: Path, value: object) -> None:
    target = path if path.is_absolute() else ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookup-manifest", type=Path)
    parser.add_argument("--fixture-manifest", type=Path)
    parser.add_argument("--sums", type=Path)
    args = parser.parse_args()
    if args.lookup_manifest:
        _write_json(args.lookup_manifest, build_lookup_manifest())
    if args.fixture_manifest:
        _write_json(args.fixture_manifest, build_fixture_manifest())
    if args.sums:
        target = args.sums if args.sums.is_absolute() else ROOT / args.sums
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_sums(), encoding="utf-8")
    print(
        json.dumps(
            {
                "lookup_manifest": build_lookup_manifest(),
                "fixture_manifest": build_fixture_manifest(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
