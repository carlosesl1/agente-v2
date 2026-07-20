from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.run_phase6_faults import (
    CONTENTION_DOMAINS,
    _contention_violations,
    run_contention,
)

SEED = 2026071906
EXPECTED_DOMAINS = (
    "handoff_incident",
    "payment_command",
    "global_evidence_claim",
    "payment_outbox",
)


class Phase6ConcurrencyTests(unittest.TestCase):
    def test_contention_domain_manifest_is_closed_and_independent(self) -> None:
        self.assertEqual(CONTENTION_DOMAINS, EXPECTED_DOMAINS)
        self.assertEqual(len(set(CONTENTION_DOMAINS)), 4)

    def test_four_real_multiprocess_contention_domains_have_one_winner_per_round(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-contention-") as directory:
            report = run_contention(seed=SEED, rounds=2, workdir=Path(directory))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["phase"], "phase-06-handoff-and-payments")
        self.assertEqual(report["result"], "passed")
        self.assertEqual(report["violations"], 0)
        self.assertEqual(report["configuration"], {"seed": SEED, "rounds": 2})
        self.assertEqual(report["domain_rounds"], {domain: 2 for domain in EXPECTED_DOMAINS})
        self.assertEqual(report["domain_winners"], {domain: 2 for domain in EXPECTED_DOMAINS})
        self.assertEqual(len(report["round_results"]), 8)
        for row in report["round_results"]:
            with self.subTest(domain=row["domain"], round=row["round"]):
                self.assertEqual(row["winners"], 1)
                self.assertEqual(row["winning_tokens"], [1])
                self.assertEqual(row["provider_delta"], 0)
                self.assertEqual(row["partial_transactions"], 0)
                self.assertEqual(row["child_errors"], 0)
                self.assertEqual(row["nonzero_child_exits"], 0)
                self.assertEqual(row["violations"], [])

    def test_contention_envelope_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-contention-a-") as first_dir:
            first = run_contention(seed=SEED, rounds=2, workdir=Path(first_dir))
        with tempfile.TemporaryDirectory(prefix="phase6-contention-b-") as second_dir:
            second = run_contention(seed=SEED, rounds=2, workdir=Path(second_dir))
        self.assertEqual(first, second)

    def test_contention_oracle_rejects_false_winner_token_provider_and_child_claims(self) -> None:
        valid = {
            "domain": "payment_command",
            "winners": 1,
            "winning_tokens": [1],
            "provider_delta": 0,
            "partial_transactions": 0,
            "child_errors": 0,
            "nonzero_child_exits": 0,
        }
        self.assertEqual(_contention_violations(valid), ())
        for changes in (
            {"winners": 2},
            {"winning_tokens": [2]},
            {"provider_delta": 1},
            {"partial_transactions": 1},
            {"child_errors": 1},
            {"nonzero_child_exits": 1},
            {"domain": "unknown"},
        ):
            self.assertTrue(_contention_violations({**valid, **changes}), changes)


if __name__ == "__main__":
    unittest.main()
