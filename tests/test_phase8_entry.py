"""Phase 8 authenticated contract-replacement and quarantine tests."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
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

    def test_manifest_preserves_all_nine_rejected_identities(self) -> None:
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

        self.assertEqual(len(rejected), 9)
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "cded17b8bf5e813ef2d0523b749b14cba312caa5113be4990f28b0c5c3136ed3",
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
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for token in forbidden:
                    self.assertNotIn(token, text, f"{relative}: {token}")

    def test_phase_index_keeps_slice_zero_and_rollout_closed(self) -> None:
        text = (ROOT / "docs/refactor/README.md").read_text(encoding="utf-8")
        self.assertIn("7. Migração das fronteiras | **concluída", text)
        self.assertIn("8. Shadow, canary e rollout | **design aprovado", text)
        self.assertIn("Slice 0 bloqueado", text)
        self.assertIn("9. Remoção do legado | bloqueada", text)


if __name__ == "__main__":
    unittest.main()
