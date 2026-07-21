"""Phase 8 authenticated contract-replacement and quarantine tests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/refactor/evidence/phase-08"
QUARANTINE = EVIDENCE / "quarantine-manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict[str, object]:
    return json.loads(QUARANTINE.read_text(encoding="utf-8"))


def _git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _read_scanned_text(path: Path) -> str:
    """Decode every covered file strictly; decode failure blocks the gate."""
    return path.read_text(encoding="utf-8", errors="strict")


class Phase8EntryTests(unittest.TestCase):
    def test_historical_entry_remains_closed_and_is_quarantined(self) -> None:
        entry = json.loads((EVIDENCE / "entry-baseline.json").read_text(encoding="utf-8"))
        manifest = _manifest()
        historical = {item["path"]: item for item in manifest["historical_inputs"]}

        self.assertEqual(entry["base_commit"], "93682024b4867d3e313324339a7060d5351dcd3d")
        self.assertEqual(entry["spec_commit"], "0dbc9cb9722762dfc4f24a3ea73bfce974835a84")
        self.assertEqual(entry["phase7_ci_run_id"], 29804123764)
        self.assertEqual(entry["phase7_review_approved"], 3)
        self.assertEqual(entry["rollout"], "NO-GO")
        self.assertFalse(entry["phase9_started"])
        self.assertEqual(
            historical["docs/refactor/evidence/phase-08/entry-baseline.json"]["classification"],
            "HISTORICAL-NON-EXECUTABLE",
        )

    def test_contract_replacement_pins_the_approved_design(self) -> None:
        manifest = _manifest()
        design = manifest["approved_design"]
        spec = ROOT / design["path"]

        self.assertEqual(design["commit"], "2889e9ec08f466bbb16a30e4bb5c9a098daf54d3")
        self.assertEqual(design["tree"], "ed57032319d2319389412f4407b268e3d7b7a78c")
        self.assertEqual(design["blob"], "0e599670b4bc585b1665d932a84afcf3c4b57456")
        self.assertEqual(
            design["sha256"],
            "0f7486191e9963b3786a83cc7096c2af12a89905c5d92fcc27edf431367dcf60",
        )
        self.assertEqual(_sha256(spec), design["sha256"])
        self.assertEqual(spec.stat().st_size, design["bytes"])
        self.assertEqual(len(spec.read_text(encoding="utf-8").splitlines()), design["lines"])
        self.assertEqual(manifest["design_review"]["verdicts"], ["Approved"] * 3)
        self.assertTrue(manifest["design_review"]["user_approved"])

    def test_replacement_plan_is_the_only_executable_plan(self) -> None:
        manifest = _manifest()
        replacement = manifest["replacement_plan"]
        plan = ROOT / replacement["path"]
        plan_bytes = plan.read_bytes()

        self.assertEqual(
            replacement["path"],
            "docs/superpowers/plans/2026-07-21-phase-8-operational-boundary-correction.md",
        )
        self.assertEqual(hashlib.sha256(plan_bytes).hexdigest(), replacement["sha256"])
        self.assertEqual(len(plan_bytes), replacement["bytes"])
        self.assertEqual(len(plan_bytes.decode("utf-8").splitlines()), replacement["lines"])
        self.assertIn("# Fase 8 — Correção da Fronteira Operacional — Implementation Plan", plan_bytes.decode())
        self.assertFalse(manifest["implementation_authorized"])
        self.assertFalse(manifest["source_implementation_started"])
        self.assertFalse(manifest["wheel_build_authorized"])
        self.assertFalse(manifest["build_authorized"])
        self.assertFalse(manifest["canary_authorized"])
        self.assertFalse(manifest["conversation_gate_ready"])
        self.assertFalse(manifest["e2e_authorized"])
        self.assertEqual(manifest["rollout"], "NO-GO")
        self.assertFalse(manifest["phase9_started"])

    def test_replacement_plan_orders_all_code_before_immutable_candidates(self) -> None:
        manifest = _manifest()
        plan = (ROOT / manifest["replacement_plan"]["path"]).read_text(encoding="utf-8")
        task_numbers = [int(value) for value in re.findall(r"^## Task (\d+):", plan, re.M)]

        self.assertEqual(task_numbers, list(range(27)))
        self.assertLess(
            plan.index("## Task 21: Implementar package/release tooling"),
            plan.index("## Task 22: Congelar source F/E"),
        )
        self.assertLess(
            plan.index("## Task 22: Congelar source F/E"),
            plan.index("## Task 23: Construir e autenticar wheel"),
        )
        self.assertLess(
            plan.index("## Task 24: Criar runtime candidate3"),
            plan.index("## Task 25: Congelar runtime F/E"),
        )
        self.assertLess(
            plan.index("## Task 25: Congelar runtime F/E"),
            plan.index("## Task 26: Executar o release contract pré-build"),
        )
        self.assertIn("183fb41d645e1bb04e237c986988309a28e42b34", plan)
        post_gate = plan.split(
            "## 4. Gates pós-plano — deliberadamente não executáveis aqui",
            1,
        )[1].split("## 5. Verificação do próprio plano antes do Slice 0", 1)[0]
        self.assertNotIn("```bash", post_gate)

    def test_replacement_plan_closes_reviewed_tdd_and_owner_gaps(self) -> None:
        manifest = _manifest()
        plan = (ROOT / manifest["replacement_plan"]["path"]).read_text(encoding="utf-8")
        task_13 = plan.split("## Task 13:", 1)[1].split("## Task 14:", 1)[0]
        task_14 = plan.split("## Task 14:", 1)[1].split("## Task 15:", 1)[0]

        for literal in (
            "test_patch_paths: tuple[str, ...]",
            "execution_root_manifest_sha256",
            "env_name_allowlist: tuple[str, ...]",
            "duration_ns: int",
            "S0 = {}",
            "S1 = {owner.lock}",
            "S2 = {owner.lock, object.tmp}",
            "InternalJobExecutionLockFactory",
            "MemoryPreparationExecutionLockFactory",
            "MemoryPreparationRecoveryWorker",
            "LearningClaimsClosedReceipt",
            "SourceEventIdentity",
            "ConversationTestDispatchAuthorization",
            "BehaviorStateSnapshot",
            "CapabilityPolicy",
        ):
            self.assertIn(literal, plan)

        self.assertIn("settlement command relays", task_13)
        self.assertIn("accept_boundary_settlement", task_13)
        self.assertIn("exclusivamente para handoff e learning", task_14)
        self.assertNotIn("Handoff/settlement ingresses", task_14)
        self.assertNotIn("tests/test_dispatch.py", plan)
        self.assertNotIn("`tests.test_serialization`", plan)
        self.assertNotIn("Create package:", plan)
        self.assertIn("Modify: `reservation_execution/reconciliation.py`", plan)
        self.assertIn("Modify: `reservation_followup/reconciliation.py`", plan)
        for task in range(23):
            self.assertIn(
                f"docs/refactor/evidence/phase-08/tasks/task-{task:02d}/",
                plan,
            )

    def test_replacement_plan_keeps_historical_runtime_read_only_and_build_one_shot(self) -> None:
        manifest = _manifest()
        plan = (ROOT / manifest["replacement_plan"]["path"]).read_text(encoding="utf-8")
        task_21 = plan.split("## Task 21:", 1)[1].split("## Task 22:", 1)[0]
        task_24 = plan.split("## Task 24:", 1)[1].split("## Task 25:", 1)[0]
        task_25 = plan.split("## Task 25:", 1)[1].split("## Task 26:", 1)[0]
        task_26 = plan.split("## Task 26:", 1)[1].split("## 4. Gates pós-plano", 1)[0]

        self.assertIn("git clone --no-local --no-checkout", task_24)
        self.assertNotIn(" worktree add ", task_24)
        self.assertIn("183fb41d645e1bb04e237c986988309a28e42b34", task_24)
        self.assertIn("e546e9d88093c09a245502bcca3d119e2e450672", task_24)
        for literal in (
            "decision=GO_BUILD_ONCE",
            "authorization ID/nonce",
            "not-before/expires-at",
            "BuildAuthorizationStore.consume_once",
            "loopback registry",
            "Delete, tag overwrite e garbage",
            "collection são proibidos",
            "RootFS/layers",
        ):
            self.assertIn(literal, task_21)
        self.assertIn("--frozen --no-dev --offline", task_25)
        self.assertIn("reservation_boundary.__file__", task_25)
        self.assertIn("GO_BUILD_ONCE_ELIGIBLE|NO_GO", task_26)
        self.assertIn("não é aceito pelo", task_26)
        self.assertIn("publisher e não executa build", task_26)

    def test_replacement_plan_closes_task22_gate_and_relay_dto_interfaces(self) -> None:
        manifest = _manifest()
        plan = (ROOT / manifest["replacement_plan"]["path"]).read_text(encoding="utf-8")
        task_1 = plan.split("## Task 1:", 1)[1].split("## Task 2:", 1)[0]
        task_4 = plan.split("## Task 4:", 1)[1].split("## Task 5:", 1)[0]
        task_13 = plan.split("## Task 13:", 1)[1].split("## Task 14:", 1)[0]
        task_17 = plan.split("## Task 17:", 1)[1].split("## Task 18:", 1)[0]
        task_22 = plan.split("## Task 22:", 1)[1].split("## Task 23:", 1)[0]

        self.assertIn("Tasks 0–21", plan)
        self.assertIn("Task 22 é um gate puro", task_22)
        self.assertIn("não cria novo U/P/S/R/O", task_22)
        self.assertIn("--source-f", task_22)
        self.assertIn("--evidence-e", task_22)
        self.assertLess(task_22.index("Criar E terminal"), task_22.index("--source-f"))
        self.assertNotIn("F/E ainda não congelados/envelopes ausentes", plan)

        for literal in (
            "SettlementRelayBundle",
            "ScenarioTerminalVerificationReceipt",
            "RESERVATION_RELAY_DOMAIN = phase8-reservation-relay-bundle-v1",
            "SETTLEMENT_RELAY_DOMAIN = phase8-settlement-relay-bundle-v1",
            "SCENARIO_TERMINAL_VERIFICATION_DOMAIN = phase8-scenario-terminal-verification-v1",
        ):
            self.assertIn(literal, task_1)
        self.assertIn("bundle: ReservationRelayBundle", task_4)
        self.assertIn("bundle: SettlementRelayBundle", task_4)
        self.assertIn("-> TargetOperationReceipt", task_4)
        self.assertIn("SettlementIngressPort", task_13)
        self.assertIn("source_turn_receipt_hash: str", task_13)
        self.assertIn("-> ScenarioTerminalVerificationReceipt", task_17)
        self.assertIn("qualification_id: str", task_17)
        self.assertIn("scenario_id: str", task_17)

        self.assertIn("Create in E:", plan)
        self.assertIn("gate-input-manifest.json", task_22)
        self.assertIn("terminal-result.json", task_22)
        self.assertNotIn("Create in E: red.patch", task_22)

    def test_historical_spec_and_plan_have_non_executable_banners(self) -> None:
        manifest = _manifest()
        banner_paths = {
            item["path"]
            for item in manifest["historical_inputs"]
            if item["requires_banner"]
        }
        self.assertEqual(len(banner_paths), 2)
        self.assertEqual(
            {Path(relative).parent.as_posix() for relative in banner_paths},
            {"docs/superpowers/specs", "docs/superpowers/plans"},
        )
        for relative in banner_paths:
            first_lines = (ROOT / relative).read_text(encoding="utf-8").splitlines()[:8]
            self.assertIn("HISTORICAL-NON-EXECUTABLE", "\n".join(first_lines), relative)

    def test_manifest_preserves_all_ten_replaced_identities(self) -> None:
        manifest = _manifest()
        rejected = {
            item["path"]: item["rejected_identity_fixed_by_approved_spec"]
            for item in manifest["historical_inputs"]
        }
        canonical = json.dumps(
            rejected,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        self.assertEqual(len(rejected), 10)
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "3a42c5da37140296618b1541a7844448813ee4f93c429d0bf929deb60ffe2da0",
        )
        for item in manifest["historical_inputs"]:
            self.assertEqual(item["classification"], "HISTORICAL-NON-EXECUTABLE")
            relative = item["path"]
            before = subprocess.run(
                ["git", "show", f"{manifest['created_from_head']}:{relative}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
            expected_before = item["pre_quarantine_head_identity"]
            self.assertEqual(_git_blob_sha1(before), expected_before["blob"])
            self.assertEqual(hashlib.sha256(before).hexdigest(), expected_before["sha256"])
            self.assertEqual(len(before), expected_before["bytes"])
            self.assertEqual(len(before.decode("utf-8").splitlines()), expected_before["lines"])

            current = (ROOT / relative).read_bytes()
            expected_current = item["current_state"]
            self.assertEqual(hashlib.sha256(current).hexdigest(), expected_current["sha256"])
            self.assertEqual(len(current), expected_current["bytes"])
            self.assertEqual(
                len(current.decode("utf-8").splitlines()),
                expected_current["lines"],
            )

    def test_active_authorities_do_not_reference_quarantined_interfaces(self) -> None:
        manifest = _manifest()
        forbidden = tuple(manifest["forbidden_active_tokens"])
        active_paths = tuple(manifest["active_authority_paths"])
        exclusions = {item["path"] for item in manifest["scan_exclusions"]}
        extensions = set(manifest["active_scan_extensions"])
        self.assertGreater(len(forbidden), 0)
        self.assertGreater(len(active_paths), 0)
        for relative in active_paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, text, f"{relative}: {token}")

        for scan_root in manifest["active_scan_roots"]:
            for path in (ROOT / scan_root).rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                relative = path.relative_to(ROOT).as_posix()
                if relative in exclusions or path.suffix.lower() not in extensions:
                    continue
                text = _read_scanned_text(path)
                for token in forbidden:
                    self.assertNotIn(token, text, f"{relative}: {token}")

    def test_scanner_rejects_non_utf8_covered_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.md"
            path.write_bytes(b"\xfflegacy-phase8-turn-adapter")
            with self.assertRaises(UnicodeDecodeError):
                _read_scanned_text(path)

    def test_active_scan_exclusions_are_truthfully_classified(self) -> None:
        manifest = _manifest()
        active = set(manifest["active_authority_paths"])
        exclusions = {
            item["path"]: item["reason"]
            for item in manifest["scan_exclusions"]
        }
        active_exclusions = active.intersection(exclusions)

        self.assertEqual(
            active_exclusions,
            {
                "docs/refactor/04-phased-delivery-plan.md",
                "docs/refactor/05-validation-and-rollout.md",
                "docs/refactor/06-risk-register.md",
                "docs/refactor/decisions/0006-promote-identical-oci-digest.md",
                "docs/refactor/phases/phase-08-shadow-canary-rollout.md",
            },
        )
        for relative in active_exclusions:
            self.assertEqual(
                exclusions[relative],
                "active reconciled authority scanned separately before global scan",
            )

    def test_evidence_index_counts_all_ten_replaced_identities(self) -> None:
        index = (ROOT / "docs/refactor/evidence/README.md").read_text(encoding="utf-8")
        self.assertIn("dez identidades rejeitadas", index)
        self.assertNotIn("nove identidades rejeitadas", index)

    def test_phase_index_keeps_slice_zero_and_rollout_closed(self) -> None:
        text = (ROOT / "docs/refactor/README.md").read_text(encoding="utf-8")
        self.assertIn("7. Migração das fronteiras | **concluída", text)
        self.assertIn("8. Shadow, canary e rollout | **design aprovado", text)
        self.assertIn("Slice 0 bloqueado", text)
        self.assertIn("9. Remoção do legado | bloqueada", text)


if __name__ == "__main__":
    unittest.main()
