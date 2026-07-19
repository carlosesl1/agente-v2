#!/usr/bin/env python3
"""Run the closed Phase 5 mutation catalog in disposable repository copies."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

PHASE = "phase-05-durable-command-execution"
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    test: str


MUTANTS = (
    Mutant(
        name="remove_optimistic_revision",
        path="reservation_execution/sqlite_store.py",
        old="            if current.meta.revision != expected_revision:\n",
        new="            if False and current.meta.revision != expected_revision:\n",
        test="tests.test_phase5_sqlite_store.Phase5SQLiteStoreTests.test_apply_event_requires_exact_expected_revision_and_rolls_back",
    ),
    Mutant(
        name="accept_divergent_event_hash",
        path="reservation_execution/sqlite_store.py",
        old="        if _sha256_text(raw) != digest:\n            raise DataCorruption(\"event hash mismatch\")\n",
        new="        if False and _sha256_text(raw) != digest:\n            raise DataCorruption(\"event hash mismatch\")\n",
        test="tests.test_phase5_mutation_runner.Phase5MutationRunnerTests.test_event_hash_guard_rejects_digest_only_tamper",
    ),
    Mutant(
        name="commit_command_outside_transaction",
        path="reservation_execution/sqlite_store.py",
        old="""            for command in transition.commands:\n                self._insert_immutable_command(command)\n                self._insert_initial_ledger(command)\n""",
        new="""            for command in transition.commands:\n                self._connection.commit()\n                self._insert_immutable_command(command)\n                self._insert_initial_ledger(command)\n""",
        test="tests.test_phase5_sqlite_store.Phase5AtomicCommandTests.test_every_statement_fault_rolls_back_after_reopen",
    ),
    Mutant(
        name="remove_unique_idempotency",
        path="schemas/phase5/sqlite.sql",
        old="    CONSTRAINT uq_reservation_commands_idempotency_key UNIQUE (idempotency_key),\n",
        new="",
        test="tests.test_phase5_schema.Phase5SchemaTests.test_generated_sql_matches_tracked_artifacts_and_is_deterministic",
    ),
    Mutant(
        name="allow_second_dispatch_slot",
        path="reservation_execution/sqlite_store.py",
        old="            if ledger.dispatch_slots_consumed == 1:\n",
        new="            if False and ledger.dispatch_slots_consumed == 1:\n",
        test="tests.test_phase5_claims.Phase5ClaimTests.test_fence_persists_exact_request_and_only_one_permit",
    ),
    Mutant(
        name="ignore_fencing_token",
        path="reservation_execution/types.py",
        old='        _require_int_at_least(self.fencing_token, "lease.fencing_token", 1)\n',
        new='        _require_int_at_least(self.fencing_token, "lease.fencing_token", 0)\n',
        test="tests.test_phase5_types.Phase5TypeContractTests.test_lease_requires_opaque_owner_exact_positive_token_and_positive_ttl",
    ),
    Mutant(
        name="recover_post_fence_as_retry",
        path="reservation_execution/reconciliation.py",
        old="        unknown = self._store.mark_expired_fenced_unknown(now=now)\n",
        new="        unknown = self._store.release_expired_pre_dispatch(now=now)\n",
        test="tests.test_phase5_reconciliation.Phase5ReconciliationTests.test_expired_post_fence_becomes_unknown_atomically_without_adapter",
    ),
    Mutant(
        name="post_fence_exception_as_not_called",
        path="reservation_execution/worker.py",
        old="""        except Exception:\n            outcome = claim.command.outcome(\n                certainty=ExecutionCertainty.CALLED_UNKNOWN,\n                normalized_status=\"dispatch_exception\",\n                evidence=(permit.request_hash,),\n            )\n        if outcome.certainty is ExecutionCertainty.NOT_CALLED:\n""",
        new="""        except Exception:\n            outcome = claim.command.outcome(\n                certainty=ExecutionCertainty.NOT_CALLED,\n                normalized_status=\"dispatch_exception\",\n                evidence=(permit.request_hash,),\n            )\n        if False and outcome.certainty is ExecutionCertainty.NOT_CALLED:\n""",
        test="tests.test_phase5_worker.Phase5WorkerTests.test_exception_after_fence_becomes_unknown_and_second_run_never_dispatches",
    ),
    Mutant(
        name="allow_not_called_from_dispatch",
        path="reservation_execution/worker.py",
        old="        if outcome.certainty is ExecutionCertainty.NOT_CALLED:\n",
        new="        if False and outcome.certainty is ExecutionCertainty.NOT_CALLED:\n",
        test="tests.test_phase5_worker.Phase5WorkerTests.test_dispatch_returning_not_called_is_contract_violation_promoted_to_unknown",
    ),
    Mutant(
        name="redispatch_called_unknown",
        path="reservation_execution/worker.py",
        old="""        return WorkerResult.completed(\n            self._store.record_outcome(permit, outcome, now=now)\n        )\n""",
        new="""        transition = self._store.record_outcome(permit, outcome, now=now)\n        if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:\n            self._adapter.dispatch(\n                request,\n                idempotency_key=claim.command.idempotency_key,\n            )\n        return WorkerResult.completed(transition)\n""",
        test="tests.test_phase5_worker.Phase5WorkerTests.test_called_unknown_goes_to_manual_review_without_redispatch",
    ),
    Mutant(
        name="outbox_failure_requeues_command",
        path="reservation_execution/outbox.py",
        old="""        if delivery_failed:\n            self._store.release_outbox(claim, now=now)\n            return OutboxWorkerResult.retryable_failure(claim.message.message_id)\n""",
        new="""        if delivery_failed:\n            self._store._connection.execute(\n                \"UPDATE execution_ledger SET status='queued'\"\n            )\n            self._store.release_outbox(claim, now=now)\n            return OutboxWorkerResult.retryable_failure(claim.message.message_id)\n""",
        test="tests.test_phase5_outbox.Phase5OutboxTests.test_delivery_failure_releases_only_message",
    ),
    Mutant(
        name="mark_delivered_without_receipt",
        path="reservation_execution/outbox.py",
        old="        self._store.complete_outbox(claim, receipt, now=now)\n",
        new="""        self._store._connection.execute(\n            \"UPDATE outbox_messages SET status='delivered', claim_owner=NULL, \"\n            \"lease_acquired_at=NULL, lease_expires_at=NULL, delivered_at=?, \"\n            \"updated_at=? WHERE message_id=?\",\n            (now.isoformat(), now.isoformat(), claim.message.message_id),\n        )\n""",
        test="tests.test_phase5_outbox.Phase5OutboxTests.test_delivery_marks_receipt_without_touching_ledger",
    ),
    Mutant(
        name="accept_divergent_outcome",
        path="reservation_execution/sqlite_store.py",
        old="""        if (\n            ledger.outcome_json != raw_outcome\n            or ledger.outcome_hash != _sha256_text(raw_outcome)\n        ):\n            raise IdentityConflict(\"completed command already has a divergent outcome\")\n""",
        new="""        if False and (\n            ledger.outcome_json != raw_outcome\n            or ledger.outcome_hash != _sha256_text(raw_outcome)\n        ):\n            raise IdentityConflict(\"completed command already has a divergent outcome\")\n""",
        test="tests.test_phase5_worker.Phase5WorkerTests.test_identical_outcome_replay_is_idempotent_and_divergence_conflicts",
    ),
    Mutant(
        name="accept_tampered_command_hash",
        path="reservation_execution/sqlite_store.py",
        old="        if _sha256_text(raw) != digest:\n            raise DataCorruption(\"command hash mismatch\")\n",
        new="        if False and _sha256_text(raw) != digest:\n            raise DataCorruption(\"command hash mismatch\")\n",
        test="tests.test_phase5_mutation_runner.Phase5MutationRunnerTests.test_command_hash_guard_rejects_digest_only_tamper",
    ),
    Mutant(
        name="accept_tampered_state_hash",
        path="reservation_execution/sqlite_store.py",
        old="        if _sha256_text(raw) != digest:\n            raise DataCorruption(\"workflow state hash mismatch\")\n",
        new="        if False and _sha256_text(raw) != digest:\n            raise DataCorruption(\"workflow state hash mismatch\")\n",
        test="tests.test_phase5_sqlite_store.Phase5SQLiteStoreTests.test_tampered_state_hash_or_serialization_fails_before_reduce",
    ),
    Mutant(
        name="skip_manual_review",
        path="reservation_execution/sqlite_store.py",
        old="""            if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:\n                review = ManualReviewRequested(\n""",
        new="""            if False and outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:\n                review = ManualReviewRequested(\n""",
        test="tests.test_phase5_worker.Phase5WorkerTests.test_called_unknown_goes_to_manual_review_without_redispatch",
    ),
    Mutant(
        name="allow_effect_without_evidence",
        path="reservation_domain/types.py",
        old="""            if not normalized_evidence:\n                raise ValueError(\"effect_confirmed requires evidence\")\n""",
        new="""            if False and not normalized_evidence:\n                raise ValueError(\"effect_confirmed requires evidence\")\n""",
        test="tests.test_phase5_domain_outcomes.Phase5DomainOutcomeTests.test_effect_confirmed_requires_reference_and_evidence",
    ),
    Mutant(
        name="allow_not_called_provider_reference",
        path="reservation_domain/types.py",
        old="""        if (\n            self.certainty is ExecutionCertainty.NOT_CALLED\n            and self.provider_reference is not None\n        ):\n            raise ValueError(\"not_called forbids provider_reference\")\n""",
        new="""        if False and (\n            self.certainty is ExecutionCertainty.NOT_CALLED\n            and self.provider_reference is not None\n        ):\n            raise ValueError(\"not_called forbids provider_reference\")\n""",
        test="tests.test_phase5_domain_outcomes.Phase5DomainOutcomeTests.test_not_called_rejects_provider_reference",
    ),
    Mutant(
        name="reduce_property_gate",
        path="scripts/run_phase5_properties.py",
        old="_MIN_GATE_CASES = 20_000\n",
        new="_MIN_GATE_CASES = 1\n",
        test="tests.test_phase5_mutation_runner.Phase5MutationRunnerTests.test_property_gate_minimum_is_closed",
    ),
    Mutant(
        name="remove_required_fault_point",
        path="scripts/run_phase5_faults.py",
        old='FAULT_POINTS = (\n    "before_event",\n',
        new="FAULT_POINTS = (\n",
        test="tests.test_phase5_fault_injection.Phase5FaultInjectionTests.test_fault_point_manifest_is_closed_and_exact",
    ),
)


def _ignore_copy(path: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        candidate = Path(path) / name
        if name in {".git", ".superpowers", ".pytest_cache", "__pycache__"}:
            ignored.add(name)
        elif name.endswith((".pyc", ".pyo", "-wal", "-shm")):
            ignored.add(name)
        elif candidate.suffix in {".db", ".sqlite", ".sqlite3"}:
            ignored.add(name)
    return ignored


def _run_one(*, root: Path, mutant: Mutant) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"phase5-mutant-{mutant.name}-") as temp:
        copy_root = Path(temp) / "repo"
        shutil.copytree(root, copy_root, ignore=_ignore_copy)
        target = copy_root / mutant.path
        source = target.read_text(encoding="utf-8")
        target_count = source.count(mutant.old)
        if target_count != 1:
            return {
                "name": mutant.name,
                "path": mutant.path,
                "test": mutant.test,
                "target_count": target_count,
                "exit_code": -1,
                "killed": False,
                "error": f"mutation target count was {target_count}, expected 1",
            }
        target.write_text(source.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", mutant.test, "-v"],
                cwd=copy_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=240,
            )
            exit_code = completed.returncode
            error = None
        except subprocess.TimeoutExpired:
            exit_code = -2
            error = "mutant test timed out"
        result: dict[str, Any] = {
            "name": mutant.name,
            "path": mutant.path,
            "test": mutant.test,
            "target_count": target_count,
            "exit_code": exit_code,
            "killed": exit_code > 0,
        }
        if error is not None:
            result["error"] = error
        return result


def run_mutants(
    *,
    root: Path = ROOT,
    selected_names: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not isinstance(root, Path):
        raise TypeError("root must be a pathlib.Path")
    catalog = {mutant.name: mutant for mutant in MUTANTS}
    if len(catalog) != len(MUTANTS):
        raise ValueError("mutation catalog names must be unique")
    if selected_names is None:
        selected = MUTANTS
    else:
        unknown = sorted(set(selected_names) - set(catalog))
        if unknown:
            raise ValueError(f"unknown mutants: {unknown}")
        selected = tuple(catalog[name] for name in selected_names)
    results = [_run_one(root=root, mutant=mutant) for mutant in selected]
    return {
        "schema_version": 1,
        "phase": PHASE,
        "scope": "temporary repository copies only; working tree unchanged",
        "mutant_count": len(results),
        "catalog_count": len(MUTANTS),
        "mutants": results,
        "all_killed": bool(results) and all(item["killed"] for item in results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 5 mutation catalog")
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    try:
        selected = tuple(args.only) if args.only else None
        report = run_mutants(root=ROOT, selected_names=selected)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["all_killed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
