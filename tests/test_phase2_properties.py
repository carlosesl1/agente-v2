from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import reservation_domain.properties as property_module
import reservation_domain.reducer as reducer_module
from reservation_domain import (
    CollectingState,
    EVENT_TYPES,
    STATE_TYPES,
    StartSearch,
    Transition,
    TransitionStatus,
    new_workflow,
    reduce,
    run_property_sequences,
    transition_matrix,
)

ROOT = Path(__file__).resolve().parents[1]


class InventedState(CollectingState):
    TYPE = "invented_state"


class InventedEvent(StartSearch):
    TYPE = "invented_event"


class PropertyContractTests(unittest.TestCase):
    def test_transition_matrix_is_total(self) -> None:
        matrix = transition_matrix()
        self.assertEqual(set(matrix), {state.TYPE for state in STATE_TYPES})
        expected_events = {event.TYPE for event in EVENT_TYPES}
        for state_name, row in matrix.items():
            with self.subTest(state=state_name):
                self.assertEqual(set(row), expected_events)
                self.assertTrue(all(value in {"evaluate", "ignore"} for value in row.values()))

    def test_transition_matrix_rejects_undeclared_state_or_event(self) -> None:
        with patch.object(
            reducer_module,
            "STATE_TYPES",
            (*STATE_TYPES, InventedState),
        ):
            with self.assertRaises(ValueError):
                transition_matrix()
        with patch.object(
            reducer_module,
            "EVENT_TYPES",
            (*EVENT_TYPES, InventedEvent),
        ):
            with self.assertRaises(ValueError):
                transition_matrix()

    def test_property_smoke(self) -> None:
        report = run_property_sequences(sequences=2_000, max_events=20, seed=20260718)
        self.assertEqual(report.sequences, 2_000)
        self.assertEqual(report.violations, ())
        self.assertEqual(report.exceptions, 0)
        self.assertEqual(report.premature_commands, 0)
        self.assertEqual(report.second_commands, 0)
        self.assertEqual(report.duplicate_reemissions, 0)
        self.assertEqual(report.conflicting_duplicate_acceptances, 0)
        self.assertGreater(report.authorized_accepts, 0)
        self.assertEqual(report.missing_authorized_commands, 0)
        self.assertGreater(report.out_of_order_probes, 0)
        self.assertEqual(report.out_of_order_policy_violations, 0)
        self.assertGreater(report.lookup_positive_cases, 0)
        self.assertGreater(report.lookup_negative_cases, 0)
        self.assertGreater(report.lookup_expired_cases, 0)
        self.assertGreater(report.lookup_unavailable_cases, 0)
        self.assertGreater(report.lookup_multi_offer_cases, 0)

    def test_property_gate_detects_missing_required_command(self) -> None:
        real_reduce = reduce

        def suppress_commands(state, event):
            transition = real_reduce(state, event)
            return replace(transition, commands=())

        with patch.object(property_module, "reduce", suppress_commands):
            report = run_property_sequences(
                sequences=500,
                max_events=20,
                seed=20260718,
            )
        self.assertGreater(report.missing_authorized_commands, 0)
        self.assertTrue(report.violations)

    def test_property_gate_detects_out_of_order_policy_mutation(self) -> None:
        real_reduce = reduce

        def accept_late_event(state, event):
            if event.occurred_at < state.meta.last_event_at:
                reset = new_workflow(
                    workflow_id=state.meta.workflow_id,
                    started_at=state.meta.last_event_at,
                )
                return Transition(
                    state=reset,
                    status=TransitionStatus.APPLIED,
                    reason="mutated_out_of_order",
                )
            return real_reduce(state, event)

        with patch.object(property_module, "reduce", accept_late_event):
            report = run_property_sequences(
                sequences=200,
                max_events=20,
                seed=20260718,
            )
        self.assertGreater(report.out_of_order_policy_violations, 0)
        self.assertTrue(report.violations)

    def test_cli_gate_rejects_trivial_workload_but_smoke_allows_it(self) -> None:
        base = [
            sys.executable,
            str(ROOT / "scripts" / "run_phase2_properties.py"),
            "--sequences",
            "1",
            "--max-events",
            "1",
            "--seed",
            "20260718",
        ]
        gate = subprocess.run(base, cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(gate.returncode, 0)
        self.assertIn("gate requires", gate.stderr)
        smoke = subprocess.run(
            [*base, "--smoke"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(smoke.returncode, 0, smoke.stderr)


if __name__ == "__main__":
    unittest.main()
