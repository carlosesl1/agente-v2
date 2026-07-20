"""Phase 7 entry and closeout contract tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import tomllib
import unittest

from scripts.generate_phase7_manifest import (
    MANIFEST_PATH,
    SHA256SUMS_PATH,
    build_manifest,
    render_sha256sums,
)
from scripts.validate_phase7 import (
    _claims_are_closed,
    _loads_json_strict,
    _remote_ci_is_authentic,
    _runtime_source_is_authentic,
    validate_phase7,
)


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def read_json(relative: str) -> dict[str, object]:
    payload = json.loads(read(relative))
    if type(payload) is not dict:
        raise AssertionError(f"{relative} must contain a JSON object")
    return payload


class Phase7EntryContractTests(unittest.TestCase):
    def test_entry_pins_base_spec_and_single_heavy_window(self) -> None:
        phase = read("docs/refactor/phases/phase-07-boundary-migration.md")
        self.assertIn("4169c6149f76e8bf4f30a26ee9d0bfbc43a58984", phase)
        self.assertIn("580b1da3602308c16c8a45af694fe6c804ce7ffb", phase)
        self.assertIn("uma janela", phase.casefold())
        self.assertIn("NO-GO", phase)
        self.assertIn("phase8_started=false", phase)

    def test_entry_evidence_is_real_and_focused(self) -> None:
        payload = read_json("docs/refactor/evidence/phase-07/entry-baseline.json")
        self.assertEqual(payload["focused_tests"], 14)
        self.assertEqual(payload["focused_failures"], 0)
        self.assertEqual(payload["phase6_validator"], "passed")
        self.assertEqual(payload["phase6_manifest"], "passed")
        self.assertEqual(payload["runtime_original_status_entries"], 80)

    def test_wheel_bootstrap_is_closed_and_stdlib_only(self) -> None:
        payload = tomllib.loads(read("pyproject.toml"))
        self.assertEqual(payload["project"]["dependencies"], [])
        self.assertNotIn("build-system", payload)
        self.assertEqual(payload["project"]["version"], "0.7.0")
        self.assertTrue((ROOT / "scripts/build_phase7_wheel.py").is_file())
        import reservation_boundary

        self.assertEqual(reservation_boundary.__version__, "0.7.0")

class Phase7CloseoutContractTests(unittest.TestCase):
    def test_workflow_triggers_only_frozen_phase7_branch(self) -> None:
        workflow = read(".github/workflows/phase7.yml")
        self.assertIn("name: phase-7-boundary-migration", workflow)
        self.assertRegex(
            workflow,
            r"(?s)on:\s*\n\s*push:\s*\n\s*branches:\s*\n\s*- phase7-boundary-migration",
        )
        self.assertNotIn("pull_request:", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*- main\s*$")

    def test_workflow_has_one_nonduplicated_heavy_gate(self) -> None:
        workflow = read(".github/workflows/phase7.yml")
        for job in (
            "static",
            "full-suite",
            "phase7-properties",
            "phase7-faults",
            "phase7-mutations",
            "gate",
        ):
            self.assertEqual(
                len(re.findall(rf"(?m)^  {re.escape(job)}:\s*$", workflow)), 1
            )
        self.assertIn("python3 -B -m unittest discover -s tests -v", workflow)
        self.assertIn("scripts/run_phase7_properties.py --cases 20000", workflow)
        self.assertIn("scripts/run_phase7_faults.py --integral", workflow)
        self.assertIn("scripts/run_phase7_mutations.py --integral", workflow)
        self.assertNotIn("chapada-leads-hermes", workflow)

    def test_workflow_and_validator_forbid_live_capabilities(self) -> None:
        payload = read(".github/workflows/phase7.yml") + read(
            "scripts/validate_phase7.py"
        )
        for token in (
            "OPENAI_API_KEY",
            "SUPABASE_URL",
            "REDIS_URL",
            "docker compose",
            "ManyChat",
            "Stripe API",
            "Wise API",
            "Cloudbeds API",
            "Bokun API",
        ):
            self.assertNotIn(token, payload)

    def test_manifest_is_deterministic_current_and_covers_runtime_patch(self) -> None:
        manifest = json.loads(read(str(MANIFEST_PATH)))
        self.assertEqual(manifest, build_manifest())
        self.assertEqual(read(str(SHA256SUMS_PATH)), render_sha256sums(manifest))
        paths = {row["path"] for row in manifest["files"]}
        self.assertIn("reservation_boundary/coordinator.py", paths)
        self.assertIn("reservation_boundary/dispatch.py", paths)
        self.assertIn(
            "docs/refactor/evidence/phase-07/runtime-integration.patch", paths
        )
        self.assertIn(".github/workflows/phase7.yml", paths)
        self.assertNotIn("docs/refactor/evidence/phase-07/ci-result.json", paths)
        self.assertEqual(manifest["rollout"], "NO-GO")
        self.assertFalse(manifest["phase8_started"])

    def test_pre_freeze_validator_passes_but_terminal_gate_is_closed(self) -> None:
        pre = validate_phase7(terminal=False)
        self.assertEqual(pre["result"], "passed")
        self.assertFalse(pre["terminal_ready"])
        self.assertEqual(pre["live_capabilities_executed"], [])
        self.assertEqual(pre["rollout"], "NO-GO")
        terminal = validate_phase7(terminal=True)
        self.assertEqual(terminal["result"], "failed")
        self.assertFalse(terminal["terminal_ready"])
        self.assertIn("candidate.json", terminal["missing_terminal_artifacts"])
        self.assertIn("ci-result.json", terminal["missing_terminal_artifacts"])

    def test_runtime_patch_and_contracts_are_bound_to_integration_tree(self) -> None:
        manifest = read_json(
            "docs/refactor/evidence/phase-07/runtime-integration-manifest.json"
        )
        self.assertTrue(manifest["patch_applies"])
        self.assertEqual(
            manifest["integration_tree"],
            "207a71c07688a63ad60d572e9b7b0c150dc585a0",
        )
        self.assertEqual(
            manifest["changed_file_count"], len(manifest["changed_files"])
        )
        self.assertEqual(manifest["live_capabilities_executed"], [])
        self.assertEqual(manifest["rollout"], "NO-GO")

    def test_phase_docs_keep_phase8_and_rollout_closed(self) -> None:
        phase = read("docs/refactor/phases/phase-07-boundary-migration.md")
        evidence = read("docs/refactor/evidence/phase-07/README.md")
        self.assertIn("phase8_started=false", phase)
        self.assertIn("rollout=NO-GO", phase)
        self.assertIn("candidato ainda não congelado", evidence)
        self.assertNotIn("rollout autorizado", (phase + evidence).lower())

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: status"):
            _loads_json_strict('{"status":"completed","status":"success"}')

    def test_runtime_source_authentication_rejects_changed_fingerprints(self) -> None:
        source = read_json(
            "docs/refactor/evidence/phase-07/runtime-source-manifest.json"
        )
        self.assertTrue(_runtime_source_is_authentic(source))
        for field in (
            "source_head",
            "source_tree",
            "source_status_hash",
            "source_tracked_diff_hash",
            "synthetic_baseline_commit",
            "synthetic_baseline_tree",
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(source)
                changed[field] = "0" * len(str(source[field]))
                self.assertFalse(_runtime_source_is_authentic(changed))
        changed = copy.deepcopy(source)
        changed["source_status_entries"] += 1
        self.assertFalse(_runtime_source_is_authentic(changed))
        changed = copy.deepcopy(source)
        changed["live_capabilities_executed"] = ["synthetic-network"]
        self.assertFalse(_runtime_source_is_authentic(changed))

    def test_remote_ci_authentication_rejects_synthetic_or_mismatched_ids(self) -> None:
        commit = "a" * 40
        jobs = [
            {
                "conclusion": "success",
                "id": index + 10,
                "name": name,
                "status": "completed",
                "url": f"https://github.com/example/repo/actions/runs/42/job/{index + 10}",
            }
            for index, name in enumerate(
                (
                    "static",
                    "full-suite",
                    "phase7-properties",
                    "phase7-faults",
                    "phase7-mutations",
                    "gate",
                )
            )
        ]
        ci = {
            "conclusion": "success",
            "head_sha": commit,
            "jobs": jobs,
            "run_id": 42,
            "run_url": "https://github.com/example/repo/actions/runs/42",
            "status": "completed",
            "workflow_path": ".github/workflows/phase7.yml",
        }
        self.assertTrue(_remote_ci_is_authentic(ci, commit))
        for field, value in (
            ("head_sha", "b" * 40),
            ("run_id", "42"),
            ("run_url", "https://example.invalid/actions/runs/42"),
            ("workflow_path", ".github/workflows/other.yml"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(ci)
                changed[field] = value
                self.assertFalse(_remote_ci_is_authentic(changed, commit))
        changed = copy.deepcopy(ci)
        changed["jobs"][0]["id"] = "10"
        self.assertFalse(_remote_ci_is_authentic(changed, commit))

    def test_rollout_and_phase8_claims_are_closed(self) -> None:
        self.assertTrue(
            _claims_are_closed({"phase8_started": False, "rollout": "NO-GO"})
        )
        self.assertFalse(
            _claims_are_closed({"phase8_started": True, "rollout": "NO-GO"})
        )
        self.assertFalse(
            _claims_are_closed({"phase8_started": False, "rollout": "GO"})
        )


if __name__ == "__main__":
    unittest.main()
