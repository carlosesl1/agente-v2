"""Phase 8 authenticated entry contract tests."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Phase8EntryTests(unittest.TestCase):
    def test_entry_pins_published_phase7_and_keeps_rollout_closed(self) -> None:
        entry = json.loads(
            (ROOT / "docs/refactor/evidence/phase-08/entry-baseline.json").read_text()
        )
        self.assertEqual(entry["base_commit"], "93682024b4867d3e313324339a7060d5351dcd3d")
        self.assertEqual(entry["spec_commit"], "0dbc9cb9722762dfc4f24a3ea73bfce974835a84")
        self.assertEqual(entry["phase7_ci_run_id"], 29804123764)
        self.assertEqual(entry["phase7_review_approved"], 3)
        self.assertEqual(entry["rollout"], "NO-GO")
        self.assertFalse(entry["phase9_started"])

    def test_phase_index_has_one_active_phase(self) -> None:
        text = (ROOT / "docs/refactor/README.md").read_text()
        self.assertIn("7. Migração das fronteiras | **concluída", text)
        self.assertIn("8. Shadow, canary e rollout | **ativa — design/plano", text)
        self.assertIn("9. Remoção do legado | bloqueada", text)


if __name__ == "__main__":
    unittest.main()
