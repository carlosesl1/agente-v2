#!/usr/bin/env python3
"""Generate deterministic Phase 4 package, fixture and checksum manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE = "phase-04-single-summary-and-confirmation"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(path: Path) -> dict[str, object]:
    return {
        "path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def build_package_manifest() -> dict[str, object]:
    paths = sorted((ROOT / "reservation_confirmation").glob("*.py"))
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "package": "reservation_confirmation",
        "python_file_count": len(paths),
        "files": [_entry(path) for path in paths],
    }


def build_fixture_manifest() -> dict[str, object]:
    paths = sorted((ROOT / "tests" / "fixtures" / "phase4").glob("*.json"))
    case_count = 0
    locales: set[str] = set()
    categories: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload.get("cases", [])
        case_count += len(cases)
        locales.update(str(item.get("locale")) for item in cases)
        categories.update(str(item.get("category")) for item in cases)
    return {
        "schema_version": 1,
        "phase": PHASE,
        "hash_algorithm": "sha256",
        "synthetic_sanitized_only": True,
        "fixture_count": len(paths),
        "case_count": case_count,
        "locales": sorted(locales),
        "categories": sorted(categories),
        "files": [_entry(path) for path in paths],
    }


def checksum_paths() -> tuple[Path, ...]:
    relative = (
        "README.md",
        ".github/workflows/phase4.yml",
        "docs/refactor/README.md",
        "docs/refactor/evidence/README.md",
        "reservation_domain/types.py",
        "reservation_domain/reducer.py",
        "reservation_domain/properties.py",
        "reservation_confirmation/README.md",
        "reservation_confirmation/__init__.py",
        "reservation_confirmation/types.py",
        "reservation_confirmation/renderer.py",
        "reservation_confirmation/presentation.py",
        "reservation_confirmation/classifier.py",
        "reservation_confirmation/binding.py",
        "reservation_confirmation/properties.py",
        "scripts/run_phase4_properties.py",
        "scripts/run_phase4_mutations.py",
        "scripts/generate_phase4_manifest.py",
        "scripts/validate_phase4.py",
        "tests/test_phase2_serialization.py",
        "tests/test_phase4_types.py",
        "tests/test_phase4_renderer.py",
        "tests/test_phase4_classifier.py",
        "tests/test_phase4_adjustment_state.py",
        "tests/test_phase4_replays.py",
        "tests/test_phase4_properties.py",
        "tests/test_phase4_mutation_runner.py",
        "tests/fixtures/phase4/confirmation-corpus.json",
        "docs/refactor/domain/phase2-domain-contract.md",
        "docs/refactor/domain/phase2-state-event-matrix.md",
        "docs/refactor/evidence/phase-03/lookup-manifest.json",
        "docs/refactor/phases/phase-04-single-summary-and-confirmation.md",
        "docs/refactor/evidence/phase-04/entry-baseline.json",
        "docs/refactor/evidence/phase-04/red-result-types.json",
        "docs/refactor/evidence/phase-04/red-result-renderer.json",
        "docs/refactor/evidence/phase-04/red-result-classifier.json",
        "docs/refactor/evidence/phase-04/red-result-adjustment.json",
        "docs/refactor/evidence/phase-04/red-result-replays.json",
        "docs/refactor/evidence/phase-04/red-result-properties.json",
        "docs/refactor/evidence/phase-04/red-result-mutations.json",
        "docs/refactor/evidence/phase-04/red-result-late-classifier.json",
        "docs/refactor/evidence/phase-04/red-result-property-baseline-review.json",
        "docs/refactor/evidence/phase-04/red-result-confirmation-identity-review.json",
        "docs/refactor/evidence/phase-04/red-result-public-property-api-review.json",
        "docs/refactor/evidence/phase-04/property-result.json",
        "docs/refactor/evidence/phase-04/mutation-result.json",
        "docs/refactor/evidence/phase-04/performance-result.json",
        "docs/refactor/evidence/phase-04/source-map.json",
        "docs/refactor/evidence/phase-04/package-manifest.json",
        "docs/refactor/evidence/phase-04/fixture-manifest.json",
        "docs/refactor/evidence/phase-04/adversarial-review.md",
        "docs/refactor/evidence/phase-04/README.md",
        "docs/refactor/evidence/phase-04/ci-result.json",
        "docs/superpowers/specs/2026-07-19-phase-4-summary-confirmation-design.md",
        "docs/superpowers/plans/2026-07-19-phase-4-summary-confirmation.md",
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
    parser.add_argument("--package-manifest", type=Path)
    parser.add_argument("--fixture-manifest", type=Path)
    parser.add_argument("--sums", type=Path)
    args = parser.parse_args()
    if args.package_manifest:
        _write_json(args.package_manifest, build_package_manifest())
    if args.fixture_manifest:
        _write_json(args.fixture_manifest, build_fixture_manifest())
    if args.sums:
        target = args.sums if args.sums.is_absolute() else ROOT / args.sums
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_sums(), encoding="utf-8")
    print(
        json.dumps(
            {
                "package_manifest": build_package_manifest(),
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
