from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.run_phase5_faults import _contention_violations, run_contention

SEED = 2026071905


class Phase5ConcurrencyTests(unittest.TestCase):
    def test_multiprocess_command_and_outbox_claims_have_one_winner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-contention-") as directory:
            report = run_contention(
                seed=SEED,
                rounds=2,
                workdir=Path(directory),
            )

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["phase"], "phase-05-durable-command-execution")
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["violations"], 0)
        self.assertEqual(report["configuration"]["rounds"], 2)
        self.assertEqual(report["command_rounds"], 2)
        self.assertEqual(report["outbox_rounds"], 2)
        self.assertEqual(report["command_claim_winners"], 2)
        self.assertEqual(report["outbox_claim_winners"], 2)
        self.assertLessEqual(report["max_provider_calls_per_round"], 1)
        self.assertEqual(report["partial_transactions"], 0)

    def test_contention_envelope_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase5-contention-a-") as first_dir:
            first = run_contention(seed=SEED, rounds=2, workdir=Path(first_dir))
        with tempfile.TemporaryDirectory(prefix="phase5-contention-b-") as second_dir:
            second = run_contention(seed=SEED, rounds=2, workdir=Path(second_dir))

        self.assertEqual(first, second)
        for round_result in first["round_results"]:
            with self.subTest(kind=round_result["kind"], round=round_result["round"]):
                self.assertEqual(round_result["winners"], 1)
                self.assertEqual(round_result["winning_tokens"], [1])
                self.assertEqual(
                    round_result["provider_calls"],
                    1 if round_result["kind"] == "command" else 0,
                )
                self.assertEqual(round_result["partial_transactions"], 0)

    def test_contention_oracle_requires_exact_provider_delta_by_kind(self) -> None:
        common = {
            "winners": 1,
            "winning_tokens": [1],
            "partial_transactions": 0,
            "child_errors": 0,
            "nonzero_child_exits": 0,
        }
        missing_command_call = {
            **common,
            "kind": "command",
            "provider_calls": 0,
        }
        unexpected_outbox_call = {
            **common,
            "kind": "outbox",
            "provider_calls": 1,
        }
        self.assertIn(
            "provider_call_count",
            _contention_violations(missing_command_call),
        )
        self.assertIn(
            "provider_call_count",
            _contention_violations(unexpected_outbox_call),
        )


if __name__ == "__main__":
    unittest.main()
