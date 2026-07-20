"""Phase 7 entry and closeout contract tests."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
