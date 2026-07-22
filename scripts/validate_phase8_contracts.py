#!/usr/bin/env python3
"""Focused Phase 8 contract-lock validator."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase8_release.graph_scan import (
    ContractScanError,
    inspect_source_schema_baseline,
    scan_for_quarantined_owners,
)

QUARANTINE = ROOT / "docs/refactor/evidence/phase-08/quarantine-manifest.json"
EXPECTED_VERSIONS = (7, 5, 1)
EXPECTED_TABLES = (
    (
        "boundary_state",
        "boundary_events",
        "boundary_commands",
        "boundary_outbox",
        "legacy_import_claims",
        "decision_comparisons",
    ),
    (
        "schema_migrations",
        "workflows",
        "domain_events",
        "reservation_commands",
        "execution_ledger",
        "outbox_messages",
    ),
    (
        "handoff_workflows",
        "handoff_events",
        "handoff_outbox",
        "handoff_receipts",
        "payment_workflows",
        "payment_events",
        "payment_evidence_claims",
        "payment_commands",
        "payment_ledger",
        "payment_outbox",
        "payment_receipts",
    ),
)


def validate() -> dict[str, object]:
    report = scan_for_quarantined_owners(ROOT, QUARANTINE)
    if report.findings:
        rendered = ", ".join(f"{item.path}:{item.token}" for item in report.findings)
        raise ContractScanError(f"quarantined active owners found: {rendered}")

    baseline = inspect_source_schema_baseline()
    versions = (
        baseline.boundary_version,
        baseline.phase5_version,
        baseline.phase6_version,
    )
    tables = (
        baseline.boundary_tables,
        baseline.phase5_tables,
        baseline.phase6_tables,
    )
    if versions != EXPECTED_VERSIONS or tables != EXPECTED_TABLES:
        raise ContractScanError("source schema baseline differs from Boundary-v7/Phase5-v5/Phase6-v1")

    return {
        "active_authorities_scanned": report.active_authorities_scanned,
        "files_scanned": report.files_scanned,
        "quarantined_owner_count": 0,
        "schema_versions": list(versions),
        "status": "PASS",
        "table_counts": [len(universe) for universe in tables],
    }


def main() -> int:
    try:
        result = validate()
    except (ContractScanError, OSError, UnicodeError, ValueError) as exc:
        print(f"phase8 contract validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
