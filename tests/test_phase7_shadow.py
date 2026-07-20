"""Independent deterministic old/new decision comparison."""

from __future__ import annotations

from dataclasses import replace
import ast
from pathlib import Path
import unittest

from reservation_boundary.shadow import (
    CRITICAL_FIELDS,
    NONCRITICAL_FIELDS,
    DecisionComparison,
    DecisionComparisonSummary,
    DecisionObservation,
    compare,
)
from reservation_boundary.types import DivergenceSeverity


ROOT = Path(__file__).resolve().parents[1]


def observation(**changes: object) -> DecisionObservation:
    values: dict[str, object] = {
        "handoff_required": False,
        "subject_signature": "a" * 64,
        "command_identities": ("command-001",),
        "dispatch_kinds": ("command",),
        "effect_certainties": ("effect_confirmed",),
        "claim_evidence": ("b" * 64,),
        "persistence_order": ("state", "event", "command", "outbox"),
        "route_label": "reservation",
        "copy_hash": "c" * 64,
        "diagnostic_tags": ("synthetic",),
    }
    values.update(changes)
    return DecisionObservation(**values)


class Phase7ShadowTests(unittest.TestCase):
    def test_closed_field_policy_covers_exact_observation(self) -> None:
        expected_critical = {
            "handoff_required",
            "subject_signature",
            "command_identities",
            "dispatch_kinds",
            "effect_certainties",
            "claim_evidence",
            "persistence_order",
        }
        expected_noncritical = {"route_label", "copy_hash", "diagnostic_tags"}
        self.assertEqual(CRITICAL_FIELDS, expected_critical)
        self.assertEqual(NONCRITICAL_FIELDS, expected_noncritical)
        self.assertEqual(
            CRITICAL_FIELDS | NONCRITICAL_FIELDS,
            set(DecisionObservation.__dataclass_fields__),
        )
        self.assertFalse(CRITICAL_FIELDS & NONCRITICAL_FIELDS)

    def test_each_authorization_identity_and_certainty_difference_is_critical(self) -> None:
        changes = {
            "handoff_required": True,
            "subject_signature": "d" * 64,
            "command_identities": ("command-002",),
            "dispatch_kinds": ("read",),
            "effect_certainties": ("called_unknown",),
            "claim_evidence": ("e" * 64,),
            "persistence_order": ("event", "state", "command", "outbox"),
        }
        old = observation()
        for field, value in changes.items():
            with self.subTest(field=field):
                result = compare(old, replace(old, **{field: value}))
                self.assertIs(result.severity, DivergenceSeverity.CRITICAL)
                self.assertEqual(result.changed_fields, (field,))

    def test_each_copy_or_diagnostic_difference_is_noncritical(self) -> None:
        changes = {
            "route_label": "handoff",
            "copy_hash": "f" * 64,
            "diagnostic_tags": ("synthetic", "new-route"),
        }
        old = observation()
        for field, value in changes.items():
            with self.subTest(field=field):
                result = compare(old, replace(old, **{field: value}))
                self.assertIs(result.severity, DivergenceSeverity.NONCRITICAL)
                self.assertEqual(result.changed_fields, (field,))

    def test_equal_observation_is_equivalent_and_hash_stable(self) -> None:
        old = observation()
        result = compare(old, observation())
        self.assertIs(result.severity, DivergenceSeverity.EQUIVALENT)
        self.assertEqual(result.changed_fields, ())
        self.assertEqual(result.old_hash, result.new_hash)
        self.assertEqual(len(result.old_hash), 64)

    def test_critical_difference_dominates_noncritical(self) -> None:
        old = observation()
        new = replace(
            old,
            handoff_required=True,
            route_label="handoff",
            copy_hash="f" * 64,
        )
        result = compare(old, new)
        self.assertIs(result.severity, DivergenceSeverity.CRITICAL)
        self.assertEqual(
            result.changed_fields,
            ("copy_hash", "handoff_required", "route_label"),
        )

    def test_summary_totals_are_reconstructed_from_rows(self) -> None:
        rows = (
            compare(observation(), observation()),
            compare(observation(), observation(copy_hash="d" * 64)),
            compare(observation(), observation(handoff_required=True)),
        )
        summary = DecisionComparisonSummary.from_rows(rows)
        self.assertEqual(
            (summary.total, summary.equivalent, summary.noncritical, summary.critical),
            (3, 1, 1, 1),
        )
        with self.assertRaises(ValueError):
            DecisionComparisonSummary(rows, 3, 2, 0, 1)

    def test_invalid_exact_types_unknown_values_and_mutation_fail_closed(self) -> None:
        with self.assertRaises(TypeError):
            observation(handoff_required=1)
        with self.assertRaises(ValueError):
            observation(dispatch_kinds=("write",))
        with self.assertRaises(ValueError):
            observation(effect_certainties=("probably",))
        with self.assertRaises(ValueError):
            observation(command_identities=("command-001", "command-001"))

        old = observation()
        object.__setattr__(old, "claim_evidence", ("not-a-hash",))
        with self.assertRaises(ValueError):
            compare(old, observation())

    def test_comparator_source_is_independent_of_old_and_new_decision_engines(self) -> None:
        source = ROOT / "reservation_boundary/shadow.py"
        tree = ast.parse(source.read_text())
        modules = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        forbidden = (
            "reservation_domain",
            "reservation_confirmation",
            "reservation_followup",
            "reservation_boundary.legacy_state",
            "reservation_boundary.coordinator",
            "reservation_boundary.dispatch",
        )
        self.assertEqual(
            [module for module in modules if module.startswith(forbidden)],
            [],
        )


if __name__ == "__main__":
    unittest.main()
