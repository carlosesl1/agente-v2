from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import unittest

from characterization.harness import (
    FAULT_POINTS,
    INCIDENT_IDS,
    ScenarioValidationError,
    load_and_replay_all,
    replay_scenario,
    scenario_paths,
)

ROOT = Path(__file__).resolve().parents[1]
CHARACTERIZATION = ROOT / "characterization"


class CharacterizationHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = scenario_paths(CHARACTERIZATION)
        cls.scenarios = [
            json.loads(path.read_text(encoding="utf-8")) for path in cls.paths
        ]
        cls.results = load_and_replay_all(CHARACTERIZATION)

    def test_exact_incident_coverage_is_present(self) -> None:
        self.assertEqual({result.incident_id for result in self.results}, set(INCIDENT_IDS))
        self.assertGreaterEqual(len(self.results), 30)
        for incident_id in INCIDENT_IDS:
            self.assertTrue(
                any(result.incident_id == incident_id for result in self.results),
                incident_id,
            )

    def test_every_scenario_replays_with_exact_expected_violations(self) -> None:
        self.assertEqual(len(self.results), len(self.paths))
        self.assertTrue(all(result.violations for result in self.results))

    def test_all_fault_boundaries_are_explicitly_characterized(self) -> None:
        observed = {point for result in self.results for point in result.fault_points}
        self.assertEqual(observed, set(FAULT_POINTS))

    def test_replays_start_from_exactly_empty_state(self) -> None:
        self.assertTrue(all(scenario["initial_state"] == {} for scenario in self.scenarios))
        scenario = deepcopy(self.scenarios[0])
        scenario["initial_state"] = {
            "metadata": {"selected_lodging_option": {"offer_id": "forbidden-preseed"}}
        }
        with self.assertRaisesRegex(ScenarioValidationError, "exactly empty state"):
            replay_scenario(scenario, characterization_root=CHARACTERIZATION)

    def test_scenario_starting_mid_fixture_is_rejected(self) -> None:
        scenario = deepcopy(self.scenarios[0])
        scenario["trace"][0]["event_id"] = "evt-hostel-choice"
        with self.assertRaisesRegex(ScenarioValidationError, "first fixture event"):
            replay_scenario(scenario, characterization_root=CHARACTERIZATION)

    def test_unknown_trace_kind_is_rejected(self) -> None:
        scenario = deepcopy(self.scenarios[0])
        scenario["trace"][1]["kind"] = "typoed_summary"
        with self.assertRaisesRegex(ScenarioValidationError, "unknown trace kinds"):
            replay_scenario(scenario, characterization_root=CHARACTERIZATION)

    def test_scenario_with_sensitive_literal_is_rejected(self) -> None:
        scenario = deepcopy(self.scenarios[0])
        scenario["source_refs"][0]["evidence"] = (
            "synthetic.person" + chr(64) + "example.invalid"
        )
        with self.assertRaisesRegex(ScenarioValidationError, "possible email"):
            replay_scenario(scenario, characterization_root=CHARACTERIZATION)

    def test_all_external_capabilities_are_closed(self) -> None:
        expected = {
            "network": False,
            "provider_reads": False,
            "provider_writes": False,
            "message_delivery": False,
            "database": False,
        }
        for scenario in self.scenarios:
            self.assertEqual(scenario["safety"], expected, scenario["case_id"])

    def test_harness_has_no_external_execution_imports(self) -> None:
        source = (CHARACTERIZATION / "harness.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        forbidden = {
            "aiohttp",
            "asyncio",
            "http",
            "requests",
            "socket",
            "sqlite3",
            "subprocess",
            "urllib",
        }
        self.assertFalse(imported.intersection(forbidden), imported)

    def test_duplicate_webhook_witness_records_two_dispatches(self) -> None:
        result = next(
            item for item in self.results if item.case_id == "f06-concurrent-duplicate-webhook"
        )
        self.assertEqual(result.metrics["inbound_events"], 3)
        self.assertEqual(result.metrics["confirmations"], 1)
        self.assertEqual(result.metrics["commands"], 2)
        self.assertEqual(result.metrics["provider_dispatches"], 2)
        self.assertIn("duplicate_provider_dispatch", result.violations)

    def test_timeout_witness_is_the_cross_field_failure(self) -> None:
        result = next(
            item for item in self.results if item.case_id == "f16-impossible-write-deadline"
        )
        self.assertEqual(result.violations, ("write_budget_impossible_by_configuration",))

    def test_unicode_label_witness_does_not_preseed_provider_identity(self) -> None:
        scenario = next(
            item for item in self.scenarios if item["case_id"] == "f15-unicode-room-label-identity"
        )
        self.assertEqual(scenario["initial_state"], {})
        selection = next(item for item in scenario["trace"] if item["kind"] == "selection")
        self.assertTrue(selection["technical_identity_equal"])
        self.assertFalse(selection["public_labels_equal"])
        self.assertFalse(selection["selected"])

    def test_historical_false_green_is_described_not_repeated(self) -> None:
        scenario = next(
            item
            for item in self.scenarios
            if item["case_id"] == "f22-preseeded-canonical-state-false-green"
        )
        self.assertEqual(scenario["initial_state"], {})
        historical = next(
            item for item in scenario["trace"] if item["kind"] == "test_fixture"
        )
        self.assertIn("selected_lodging_option", historical["historical_preseeded_fields"])


if __name__ == "__main__":
    unittest.main()
