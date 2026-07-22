"""Focused contract-lock tests for the Phase 8 bootstrap."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from phase8_release.graph_scan import (
    ContractScanError,
    assert_runtime_source_and_build_context_disjoint,
    inspect_source_schema_baseline,
    scan_for_quarantined_owners,
)


ROOT = Path(__file__).resolve().parents[1]
QUARANTINE = ROOT / "docs/refactor/evidence/phase-08/quarantine-manifest.json"


class ContractLockTests(unittest.TestCase):
    def test_quarantined_interfaces_have_zero_active_owner(self) -> None:
        report = scan_for_quarantined_owners(ROOT, QUARANTINE)
        manifest = json.loads(QUARANTINE.read_text(encoding="utf-8"))

        self.assertEqual(report.findings, ())
        self.assertEqual(
            report.active_authorities_scanned,
            len(manifest["active_authority_paths"]),
        )
        self.assertGreater(report.files_scanned, report.active_authorities_scanned)

    def test_active_owner_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            (docs / "active.md").write_text("retired.owner()\n", encoding="utf-8")
            manifest_path = root / "quarantine.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "active_authority_paths": ["docs/active.md"],
                        "active_scan_extensions": [".md"],
                        "active_scan_roots": ["docs"],
                        "forbidden_active_tokens": ["retired.owner"],
                        "scan_exclusions": [],
                    }
                ),
                encoding="utf-8",
            )

            report = scan_for_quarantined_owners(root, manifest_path)

        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].path, "docs/active.md")
        self.assertEqual(report.findings[0].token, "retired.owner")

    def test_runtime_source_and_build_context_are_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_source = root / "runtime-source"
            build_context = root / "candidate-build-context"
            runtime_source.mkdir()
            build_context.mkdir()

            separation = assert_runtime_source_and_build_context_disjoint(
                runtime_source,
                build_context,
            )

            self.assertEqual(separation.runtime_source, runtime_source.resolve())
            self.assertEqual(separation.build_context, build_context.resolve())
            with self.assertRaises(ContractScanError):
                assert_runtime_source_and_build_context_disjoint(
                    runtime_source,
                    runtime_source,
                )

    def test_source_baseline_has_boundary_v7_phase5_v5_phase6_v1(self) -> None:
        baseline = inspect_source_schema_baseline()

        self.assertEqual(baseline.boundary_version, 7)
        self.assertEqual(
            baseline.boundary_tables,
            (
                "boundary_state",
                "boundary_events",
                "boundary_commands",
                "boundary_outbox",
                "legacy_import_claims",
                "decision_comparisons",
            ),
        )
        self.assertEqual(baseline.phase5_version, 5)
        self.assertEqual(
            baseline.phase5_tables,
            (
                "schema_migrations",
                "workflows",
                "domain_events",
                "reservation_commands",
                "execution_ledger",
                "outbox_messages",
            ),
        )
        self.assertEqual(baseline.phase6_version, 1)
        self.assertEqual(
            baseline.phase6_tables,
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

    def test_contract_validator_reports_closed_source_baseline(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", "scripts/validate_phase8_contracts.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["quarantined_owner_count"], 0)
        self.assertEqual(result["schema_versions"], [7, 5, 1])
        self.assertEqual(result["table_counts"], [6, 6, 11])
