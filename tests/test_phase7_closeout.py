"""Phase 7 entry and closeout contract tests."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import tomllib
import unittest

from scripts.generate_phase7_manifest import (
    MANIFEST_PATH,
    SHA256SUMS_PATH,
    build_manifest,
    render_sha256sums,
)
from scripts.validate_phase7 import (
    EXPECTED_REMOTE_JOBS,
    _artifact_is_bound,
    _claims_are_closed,
    _fault_gate_is_authentic,
    _loads_json_strict,
    _remote_ci_is_authentic,
    _runtime_source_is_authentic,
    _terminal_artifact_checks,
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


def valid_fault_payload() -> dict[str, object]:
    domains = ("genesis", "event", "command", "outbox")
    details: list[dict[str, object]] = []
    for domain in domains:
        for index in range(50):
            values = {
                "command_rows": 1 if domain == "command" else 0,
                "conflicts": 1,
                "contenders": 2,
                "domain": domain,
                "event_rows": 0 if domain == "genesis" else 1,
                "outbox_rows": 1 if domain == "outbox" else 0,
                "passed": True,
                "round_index": index,
                "state_rows": 1,
                "winners": 1,
            }
            material = json.dumps(values, sort_keys=True, separators=(",", ":"))
            details.append(
                {**values, "detail_hash": hashlib.sha256(material.encode()).hexdigest()}
            )
    names = (
        "after_state_update",
        "after_event_insert",
        "stale_fence",
        "event_hash_conflict",
        "genesis_conflict",
        "state_hash_tamper",
    )
    return {
        "contention_details": details,
        "contention_domains": list(domains),
        "contention_rows": 200,
        "faults": [
            {
                "detail_hash": hashlib.sha256(f"phase7:{name}:1".encode()).hexdigest(),
                "name": name,
                "passed": True,
            }
            for name in names
        ],
        "passed": True,
        "restart_schedules": 2_000,
        "restarts_passed": True,
    }


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
    def test_terminal_checks_aggregate_existing_stale_ci_when_review_is_missing(self) -> None:
        source_dir = ROOT / "docs/refactor/evidence/phase-07"
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = Path(temporary)
            for name in (
                "candidate.json",
                "local-integration-result.json",
                "properties-result.json",
                "faults-result.json",
                "mutation-result.json",
            ):
                (evidence_dir / name).write_bytes((source_dir / name).read_bytes())
            (evidence_dir / "ci-result.json").write_bytes(
                (source_dir / "ci-result-invalidated-29787387850.json").read_bytes()
            )

            missing, failures = _terminal_artifact_checks(evidence_dir)

        self.assertEqual(missing, ["review-result.json"])
        self.assertIn("remote CI is not green", failures)

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

    def test_heavy_jobs_publish_raw_reports_bound_to_sha(self) -> None:
        workflow = read(".github/workflows/phase7.yml")
        self.assertEqual(workflow.count("uses: actions/upload-artifact@v4"), 3)
        for name in ("properties", "faults", "mutations"):
            with self.subTest(name=name):
                self.assertIn(f"name: phase7-{name}-${{{{ github.sha }}}}", workflow)
                self.assertIn(f"path: /tmp/phase7-{name}.json", workflow)
        self.assertEqual(workflow.count("if-no-files-found: error"), 3)
        self.assertEqual(workflow.count("retention-days: 30"), 3)

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
        self.assertIn(
            "docs/refactor/evidence/phase-07/review2-red-outputs/store-replay-outbox.txt",
            paths,
        )
        self.assertNotIn("docs/refactor/evidence/phase-07/ci-result.json", paths)
        self.assertEqual(manifest["rollout"], "NO-GO")
        self.assertFalse(manifest["phase8_started"])

    def test_evidence_validator_blocks_only_on_current_ci_after_review(self) -> None:
        pre = validate_phase7(terminal=False)
        self.assertEqual(pre["result"], "passed")
        self.assertFalse(pre["terminal_ready"])
        self.assertEqual(pre["live_capabilities_executed"], [])
        self.assertEqual(pre["rollout"], "NO-GO")
        terminal = validate_phase7(terminal=True)
        self.assertEqual(terminal["result"], "blocked")
        self.assertTrue(terminal["terminal_blocked"])
        self.assertEqual(
            terminal["blockers"],
            [
                "missing terminal artifact: ci-result.json",
            ],
        )
        self.assertFalse(terminal["terminal_ready"])
        self.assertEqual(
            terminal["missing_terminal_artifacts"],
            ["ci-result.json"],
        )

    def test_terminal_ci_binds_to_reviewed_terminal_snapshot_not_functional_parent(self) -> None:
        source_dir = ROOT / "docs/refactor/evidence/phase-07"
        terminal_snapshot = "c" * 40
        run_id = 42
        jobs = [
            {
                "conclusion": "success",
                "id": index + 10,
                "name": name,
                "status": "completed",
                "url": f"https://github.com/example/repo/actions/runs/{run_id}/job/{index + 10}",
            }
            for index, name in enumerate(EXPECTED_REMOTE_JOBS)
        ]
        ci = {
            "conclusion": "success",
            "head_sha": terminal_snapshot,
            "jobs": jobs,
            "run_id": run_id,
            "run_url": f"https://github.com/example/repo/actions/runs/{run_id}",
            "status": "completed",
            "workflow_path": ".github/workflows/phase7.yml",
        }
        with tempfile.TemporaryDirectory() as temporary:
            evidence_dir = Path(temporary)
            for name in (
                "candidate.json",
                "local-integration-result.json",
                "properties-result.json",
                "faults-result.json",
                "mutation-result.json",
                "review-result.json",
            ):
                (evidence_dir / name).write_bytes((source_dir / name).read_bytes())
            review = json.loads((evidence_dir / "review-result.json").read_text())
            review["terminal_snapshot_commit"] = terminal_snapshot
            (evidence_dir / "review-result.json").write_text(json.dumps(review))
            (evidence_dir / "ci-result.json").write_text(json.dumps(ci))

            missing, failures = _terminal_artifact_checks(evidence_dir)

        self.assertEqual(missing, [])
        self.assertEqual(failures, [])

    def test_terminal_ci_requires_explicit_valid_terminal_snapshot_binding(self) -> None:
        source_dir = ROOT / "docs/refactor/evidence/phase-07"
        candidate = read_json("docs/refactor/evidence/phase-07/candidate.json")
        candidate_commit = candidate["commit"]
        run_id = 42
        jobs = [
            {
                "conclusion": "success",
                "id": index + 10,
                "name": name,
                "status": "completed",
                "url": f"https://github.com/example/repo/actions/runs/{run_id}/job/{index + 10}",
            }
            for index, name in enumerate(EXPECTED_REMOTE_JOBS)
        ]
        ci = {
            "conclusion": "success",
            "head_sha": candidate_commit,
            "jobs": jobs,
            "run_id": run_id,
            "run_url": f"https://github.com/example/repo/actions/runs/{run_id}",
            "status": "completed",
            "workflow_path": ".github/workflows/phase7.yml",
        }
        for terminal_snapshot in (None, "absent"):
            with self.subTest(terminal_snapshot=terminal_snapshot):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence_dir = Path(temporary)
                    for name in (
                        "candidate.json",
                        "local-integration-result.json",
                        "properties-result.json",
                        "faults-result.json",
                        "mutation-result.json",
                        "review-result.json",
                    ):
                        (evidence_dir / name).write_bytes((source_dir / name).read_bytes())
                    review = json.loads((evidence_dir / "review-result.json").read_text())
                    if terminal_snapshot is None:
                        review["terminal_snapshot_commit"] = None
                    else:
                        review.pop("terminal_snapshot_commit", None)
                    (evidence_dir / "review-result.json").write_text(json.dumps(review))
                    (evidence_dir / "ci-result.json").write_text(json.dumps(ci))

                    missing, failures = _terminal_artifact_checks(evidence_dir)

                self.assertEqual(missing, [])
                self.assertIn("review terminal snapshot is invalid", failures)

    def test_runtime_patch_and_contracts_are_bound_to_integration_tree(self) -> None:
        manifest = read_json(
            "docs/refactor/evidence/phase-07/runtime-integration-manifest.json"
        )
        self.assertTrue(manifest["patch_applies"])
        candidate = read_json(
            "docs/refactor/evidence/phase-07/candidate.json"
        )
        self.assertEqual(
            manifest["integration_tree"],
            candidate["runtime_integration_tree"],
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
        self.assertIn("review-attempt-2.json", evidence)
        self.assertNotIn("rollout autorizado", (phase + evidence).lower())

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: status"):
            _loads_json_strict('{"status":"completed","status":"success"}')

    def test_terminal_artifacts_bind_exact_candidate_commit_and_tree(self) -> None:
        commit = "a" * 40
        tree = "b" * 40
        payload = {"candidate_commit": commit, "candidate_tree": tree}
        self.assertTrue(_artifact_is_bound(payload, commit, tree))
        for field, value in (("candidate_commit", "c" * 40), ("candidate_tree", "d" * 40)):
            with self.subTest(field=field):
                changed = copy.deepcopy(payload)
                changed[field] = value
                self.assertFalse(_artifact_is_bound(changed, commit, tree))
        self.assertFalse(_artifact_is_bound(payload, commit.upper(), tree))

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

    def test_fault_gate_reconstructs_every_published_contention_row(self) -> None:
        payload = valid_fault_payload()
        self.assertTrue(_fault_gate_is_authentic(payload))
        changed = copy.deepcopy(payload)
        changed["contention_details"][0]["detail_hash"] = "0" * 64
        self.assertFalse(_fault_gate_is_authentic(changed))
        changed = copy.deepcopy(payload)
        changed["contention_details"][-1] = copy.deepcopy(
            changed["contention_details"][0]
        )
        self.assertFalse(_fault_gate_is_authentic(changed))
        changed = copy.deepcopy(payload)
        changed["contention_details"][50]["command_rows"] = 1
        self.assertFalse(_fault_gate_is_authentic(changed))

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
