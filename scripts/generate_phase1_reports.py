#!/usr/bin/env python3
"""Generate deterministic Phase 1 evidence reports from the scenario corpus."""

from __future__ import annotations

from collections import defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from characterization.harness import FAULT_POINTS, INCIDENT_IDS, load_and_replay_all

CHARACTERIZATION = ROOT / "characterization"
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-01"


def _load_scenarios() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((CHARACTERIZATION / "incidents").glob("*.json"))
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_corpus_manifest() -> dict[str, Any]:
    paths = [
        CHARACTERIZATION / "harness.py",
        CHARACTERIZATION / "schema" / "scenario.schema.json",
        *sorted((CHARACTERIZATION / "fixtures").rglob("*.json")),
        *sorted((CHARACTERIZATION / "incidents").glob("*.json")),
    ]
    return {
        "schema_version": 1,
        "phase": "phase-01-incident-characterization",
        "hash_algorithm": "sha256",
        "file_count": len(paths),
        "files": [
            {
                "path": str(path.relative_to(ROOT)),
                "sha256": _sha256(path),
            }
            for path in paths
        ],
    }


def build_reports() -> tuple[dict[str, Any], dict[str, Any]]:
    scenarios = _load_scenarios()
    results = load_and_replay_all(CHARACTERIZATION)
    by_incident: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        by_incident[str(scenario["incident_id"])].append(scenario)

    coverage_rows = []
    source_rows = []
    for incident_id in sorted(INCIDENT_IDS):
        items = sorted(by_incident[incident_id], key=lambda item: item["case_id"])
        coverage_rows.append(
            {
                "incident_id": incident_id,
                "case_count": len(items),
                "cases": [item["case_id"] for item in items],
                "classifications": sorted({item["classification"] for item in items}),
                "violations": sorted(
                    {
                        violation
                        for item in items
                        for violation in item["expected_violations"]
                    }
                ),
            }
        )
        references = []
        seen = set()
        for item in items:
            for reference in item["source_refs"]:
                key = (
                    reference["path"],
                    tuple(reference["symbols"]),
                    reference["evidence"],
                )
                if key in seen:
                    continue
                seen.add(key)
                references.append(reference)
        source_rows.append({"incident_id": incident_id, "source_refs": references})

    coverage = {
        "schema_version": 1,
        "phase": "phase-01-incident-characterization",
        "scenario_count": len(results),
        "incident_count": len(by_incident),
        "incident_ids": sorted(by_incident),
        "classifications": {
            classification: sum(result.classification == classification for result in results)
            for classification in ("contract_characterized", "reproduced")
        },
        "fault_points": sorted({point for result in results for point in result.fault_points}),
        "required_fault_points": sorted(FAULT_POINTS),
        "incidents": coverage_rows,
    }
    source_map = {
        "schema_version": 1,
        "phase": "phase-01-incident-characterization",
        "source_repository": "chapada-leads-hermes",
        "source_mode": "read_only",
        "references_are_relative": True,
        "incidents": source_rows,
    }
    return coverage, source_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write canonical reports")
    args = parser.parse_args()
    coverage, source_map = build_reports()
    corpus_manifest = build_corpus_manifest()
    if args.write:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "incident-coverage.json").write_text(
            json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (EVIDENCE / "source-map.json").write_text(
            json.dumps(source_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (EVIDENCE / "corpus-manifest.json").write_text(
            json.dumps(corpus_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
