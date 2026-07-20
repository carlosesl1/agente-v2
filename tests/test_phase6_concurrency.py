from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from reservation_followup.sqlite_store import (
    ConcurrencyConflict,
    StoreUnavailable,
    _sqlite_error,
)
from scripts.run_phase6_faults import (
    CONTENTION_DOMAINS,
    _contention_violations,
    _retry_locked,
    run_contention,
)

SEED = 2026071906
EXPECTED_DOMAINS = (
    "handoff_incident",
    "payment_command",
    "global_evidence_claim",
    "payment_outbox",
)
EXPECTED_ROW_KEYS = {
    "child_error_types",
    "child_errors",
    "domain",
    "durable_owners",
    "durable_tokens",
    "durable_winners",
    "nonzero_child_exits",
    "partial_transactions",
    "provider_calls_baseline",
    "provider_calls_final",
    "provider_delta",
    "round",
    "winners",
    "winning_owners",
    "winning_tokens",
}


class Phase6ConcurrencyTests(unittest.TestCase):
    def test_sqlite_lock_is_retryable_unavailability_not_domain_conflict(self) -> None:
        attempts = []

        def locked_then_applied():
            attempts.append(len(attempts))
            if len(attempts) < 3:
                error = sqlite3.OperationalError("synthetic lock")
                error.sqlite_errorcode = (
                    sqlite3.SQLITE_BUSY
                    if len(attempts) == 1
                    else sqlite3.SQLITE_LOCKED
                )
                raise _sqlite_error(error, "contention test")
            return "applied"

        try:
            result = _retry_locked(locked_then_applied)
        except ConcurrencyConflict:
            self.fail("SQLite lock was misclassified as a domain conflict")
        self.assertEqual(result, "applied")
        self.assertEqual(len(attempts), 3)
        domain_attempts = []

        def stale_revision():
            domain_attempts.append(1)
            raise ConcurrencyConflict("stale revision")

        with self.assertRaises(ConcurrencyConflict):
            _retry_locked(stale_revision)
        self.assertEqual(len(domain_attempts), 1)
        for error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            with self.subTest(error_code=error_code):
                error = sqlite3.OperationalError("synthetic lock")
                error.sqlite_errorcode = error_code
                translated = _sqlite_error(error, "contention test")
                self.assertIs(type(translated), StoreUnavailable)
                self.assertNotIsInstance(translated, ConcurrencyConflict)
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
            "round": 0,
            "winners": 1,
            "winning_tokens": [1],
            "winning_owners": ["settlement:contender:0"],
            "durable_winners": 1,
            "durable_tokens": [1],
            "durable_owners": ["settlement:contender:0"],
            "provider_calls_baseline": 0,
            "provider_calls_final": 0,
            "provider_delta": 0,
            "partial_transactions": 0,
            "child_errors": 0,
            "child_error_types": [],
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
            {"winners": True},
            {"round": True},
            {"durable_winners": 0},
            {"durable_tokens": []},
            {"durable_owners": ["settlement:contender:1"]},
            {"provider_calls_baseline": True},
            {"provider_calls_final": 1},
            {"unexpected": 0},
        ):
            self.assertTrue(_contention_violations({**valid, **changes}), changes)
        for missing in ("round", "child_error_types", "durable_owners"):
            with self.subTest(missing=missing):
                invalid = dict(valid)
                del invalid[missing]
                self.assertTrue(_contention_violations(invalid))

    def test_contention_rows_close_schema_round_identity_and_durable_winner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase6-contention-bilateral-") as directory:
            report = run_contention(seed=SEED, rounds=1, workdir=Path(directory))
        self.assertEqual(
            set(report),
            {
                "configuration",
                "domain_rounds",
                "domain_winners",
                "domains",
                "kind",
                "phase",
                "result",
                "round_results",
                "schema_version",
                "violations",
            },
        )
        self.assertEqual(len(report["round_results"]), len(EXPECTED_DOMAINS))
        for index, row in enumerate(report["round_results"]):
            with self.subTest(index=index):
                self.assertEqual(set(row), EXPECTED_ROW_KEYS | {"violations"})
                self.assertEqual(row["domain"], EXPECTED_DOMAINS[index])
                self.assertIs(type(row["round"]), int)
                self.assertEqual(row["round"], 0)
                self.assertEqual(row["winners"], row["durable_winners"])
                self.assertEqual(row["winning_tokens"], row["durable_tokens"])
                self.assertEqual(row["winning_owners"], row["durable_owners"])
                self.assertEqual(
                    row["provider_calls_final"] - row["provider_calls_baseline"],
                    row["provider_delta"],
                )
                self.assertEqual(row["violations"], [])
        self.assertEqual(
            report["domain_rounds"],
            {
                domain: sum(row["domain"] == domain for row in report["round_results"])
                for domain in EXPECTED_DOMAINS
            },
        )
        self.assertEqual(
            report["domain_winners"],
            {
                domain: sum(
                    row["winners"]
                    for row in report["round_results"]
                    if row["domain"] == domain
                )
                for domain in EXPECTED_DOMAINS
            },
        )


if __name__ == "__main__":
    unittest.main()
