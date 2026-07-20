#!/usr/bin/env python3
"""Run the closed Phase 6 material mutation catalog in one disposable copy."""

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

PHASE = "phase-06-handoff-and-payments"
ROOT = Path(__file__).resolve().parents[1]
_TEST_RESULT_PREFIX = "__PHASE6_TEST_RESULT__"
_NON_KILLING_ERROR_TYPES = frozenset(
    {
        "builtins.ImportError",
        "builtins.IndentationError",
        "builtins.ModuleNotFoundError",
        "builtins.SyntaxError",
        "builtins.TabError",
        "unittest.loader_error",
    }
)
MUTANT_CLASSES = (
    "handoff_policy",
    "handoff_precedence",
    "payment_bootstrap",
    "method_separation",
    "global_claim",
    "amount_receiver_validation",
    "dispatch_slot",
    "post_fence_retry",
    "outbox_isolation",
    "paid_monotonicity",
    "config_closure",
    "divergent_replay",
)


@dataclass(frozen=True, slots=True)
class Mutant:
    mutation_class: str
    name: str
    path: str
    old: str
    new: str
    test: str

    def __post_init__(self) -> None:
        for field_name in (
            "mutation_class",
            "name",
            "path",
            "old",
            "new",
            "test",
        ):
            value = getattr(self, field_name)
            if type(value) is not str:
                raise TypeError(f"mutant.{field_name} must be a string")
            if not value.strip():
                raise ValueError(f"mutant.{field_name} must not be empty")
        if self.mutation_class not in MUTANT_CLASSES:
            raise ValueError("mutant.mutation_class is outside the closed catalog")
        path = Path(self.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("mutant.path must be repository-relative")
        if self.old == self.new:
            raise ValueError("mutant.old and mutant.new must differ")


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    exit_code: int
    stdout: str
    timed_out: bool


@dataclass(frozen=True, slots=True)
class _ClassifiedRun:
    verdict: str
    killed: bool
    loader_error: bool
    tests_run: int
    failures: int
    errors: int
    error: str | None


_STRUCTURED_UNITTEST_RUNNER = r"""
import json
import sys
import unittest

class TypedTextTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_types = []

    def addError(self, test, err):
        error_type = err[0]
        self.error_types.append(
            f"{error_type.__module__}.{error_type.__qualname__}"
        )
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None and not issubclass(err[0], test.failureException):
            error_type = err[0]
            self.error_types.append(
                f"{error_type.__module__}.{error_type.__qualname__}"
            )
        super().addSubTest(test, subtest, err)

name = sys.argv[1]
loader = unittest.TestLoader()
suite = loader.loadTestsFromName(name)
if loader.errors:
    print(
        "__PHASE6_TEST_RESULT__"
        + json.dumps(
            {
                "loader_error": True,
                "tests_run": 0,
                "failures": 0,
                "errors": len(loader.errors),
                "error_types": ["unittest.loader_error"] * len(loader.errors),
                "successful": False,
            },
            sort_keys=True,
        )
    )
    raise SystemExit(1)
result = unittest.TextTestRunner(
    stream=sys.stderr,
    verbosity=2,
    resultclass=TypedTextTestResult,
).run(suite)
print(
    "__PHASE6_TEST_RESULT__"
    + json.dumps(
        {
            "loader_error": False,
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "error_types": result.error_types,
            "successful": result.wasSuccessful(),
        },
        sort_keys=True,
    )
)
raise SystemExit(0 if result.wasSuccessful() else 1)
"""


MUTANTS = (
    Mutant(
        mutation_class="handoff_policy",
        name="email_disabled_blocks_required_handoff",
        path="reservation_followup/handoff.py",
        old="    jobs = [HandoffEffectJob.customer_acknowledgement(state)]\n",
        new=(
            "    jobs = (\n"
            "        []\n"
            "        if policy.internal_email is EffectRequirement.DISABLED\n"
            "        else [HandoffEffectJob.customer_acknowledgement(state)]\n"
            "    )\n"
        ),
        test=(
            "tests.test_phase6_handoff.Phase6HandoffReducerTests."
            "test_email_disabled_still_opens_queue_and_customer_ack"
        ),
    ),
    Mutant(
        mutation_class="handoff_precedence",
        name="resurface_stale_followup_after_handoff",
        path="reservation_followup/handoff.py",
        old='        public_text=" ".join((reservation_text, handoff_text)),\n',
        new=(
            '        public_text=" ".join(\n'
            "            filter(None, (reservation_text, handoff_text, prior_followup_text))\n"
            "        ),\n"
        ),
        test=(
            "tests.test_phase6_handoff.Phase6HandoffProjectionTests."
            "test_terminal_handoff_suppresses_stale_confirmation_and_missing_slots"
        ),
    ),
    Mutant(
        mutation_class="payment_bootstrap",
        name="accept_nonconfirmed_reservation_outcome",
        path="reservation_followup/types.py",
        old=(
            "        if outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED:\n"
            "            raise ValueError(\"confirmed anchor requires effect_confirmed outcome\")\n"
        ),
        new=(
            "        if False and outcome.certainty is not ExecutionCertainty.EFFECT_CONFIRMED:\n"
            "            raise ValueError(\"confirmed anchor requires effect_confirmed outcome\")\n"
        ),
        test=(
            "tests.test_phase6_payment.Phase6PaymentEvidenceTests."
            "test_only_effect_confirmed_anchor_can_bootstrap_payment"
        ),
    ),
    Mutant(
        mutation_class="method_separation",
        name="treat_wise_and_stripe_as_pix",
        path="reservation_followup/payment.py",
        old=(
            "        PaymentMethod.WISE: VerifiedWiseCredit,\n"
            "        PaymentMethod.STRIPE: VerifiedStripeEvent,\n"
        ),
        new=(
            "        PaymentMethod.WISE: PixVisualEvidence,\n"
            "        PaymentMethod.STRIPE: PixVisualEvidence,\n"
        ),
        test=(
            "tests.test_phase6_payment.Phase6PaymentEvidenceTests."
            "test_method_profiles_come_from_exact_trusted_configuration"
        ),
    ),
    Mutant(
        mutation_class="global_claim",
        name="remove_global_payment_evidence_claim",
        path="reservation_followup/sqlite_store.py",
        old="            if existing_claim is not None:\n",
        new="            if False and existing_claim is not None:\n",
        test=(
            "tests.test_phase6_payment_claims.Phase6PaymentClaimTests."
            "test_pix_claim_is_global_across_target_business_unit_and_caller_keys"
        ),
    ),
    Mutant(
        mutation_class="amount_receiver_validation",
        name="accept_divergent_pix_economics_and_receiver",
        path="reservation_followup/payment.py",
        old=(
            "    if evidence.proof_amount_minor != subject.amount_minor:\n"
            "        raise ValueError(\"Pix proof amount does not match payment subject\")\n"
            "    if evidence.proof_currency != subject.currency:\n"
            "        raise ValueError(\"Pix proof currency does not match payment subject\")\n"
            "    if subject.receiver_profile_id != trust.pix_receiver_profile_id:\n"
            "        raise ValueError(\"payment receiver does not match trusted Pix configuration\")\n"
            "    if evidence.proof_receiver_profile_id != trust.pix_receiver_profile_id:\n"
            "        raise ValueError(\"Pix receiver profile does not match trusted configuration\")\n"
        ),
        new=(
            "    if False and evidence.proof_amount_minor != subject.amount_minor:\n"
            "        raise ValueError(\"Pix proof amount does not match payment subject\")\n"
            "    if False and evidence.proof_currency != subject.currency:\n"
            "        raise ValueError(\"Pix proof currency does not match payment subject\")\n"
            "    if False and subject.receiver_profile_id != trust.pix_receiver_profile_id:\n"
            "        raise ValueError(\"payment receiver does not match trusted Pix configuration\")\n"
            "    if False and evidence.proof_receiver_profile_id != trust.pix_receiver_profile_id:\n"
            "        raise ValueError(\"Pix receiver profile does not match trusted configuration\")\n"
        ),
        test=(
            "tests.test_phase6_payment.Phase6PaymentEvidenceTests."
            "test_pix_rejects_mismatch_pending_placeholder_entropy_and_hash"
        ),
    ),
    Mutant(
        mutation_class="dispatch_slot",
        name="allow_second_dispatch_slot",
        path="reservation_followup/schema.py",
        old='            "dispatch_slots_consumed >= 0 AND dispatch_slots_consumed <= 1",\n',
        new='            "dispatch_slots_consumed >= 0 AND dispatch_slots_consumed <= 2",\n',
        test=(
            "tests.test_phase6_schema.Phase6SchemaTests."
            "test_render_is_deterministic_tracked_and_contains_only_create_tables"
        ),
    ),
    Mutant(
        mutation_class="post_fence_retry",
        name="recover_post_fence_as_retryable",
        path="reservation_followup/reconciliation.py",
        old=(
            "                SettlementRecoveryDisposition.POST_FENCE_MANUAL_REVIEW,\n"
            "                post_fence,\n"
        ),
        new=(
            "                SettlementRecoveryDisposition.PRE_FENCE_REQUEUED,\n"
            "                post_fence,\n"
        ),
        test=(
            "tests.test_phase6_reconciliation.Phase6PaymentReconciliationTests."
            "test_post_fence_recovery_is_one_shot_and_never_changes_slot_or_calls_port"
        ),
    ),
    Mutant(
        mutation_class="outbox_isolation",
        name="payment_outbox_rewrites_settlement_ledger",
        path="reservation_followup/workers.py",
        old=(
            "        if delivery_failed:\n"
            "            self._store.release_payment_outbox(claim, now=now)\n"
        ),
        new=(
            "        if delivery_failed:\n"
            "            self._store.release_payment_outbox(claim, now=now)\n"
            "            self._store._connection.execute(\n"
            "                \"UPDATE main.payment_ledger \"\n"
            "                \"SET claim_count=claim_count+1 WHERE settlement_command_id=?\",\n"
            "                (claim.settlement_command_id,),\n"
            "            )\n"
        ),
        test=(
            "tests.test_phase6_payment_outbox.Phase6PaymentOutboxTests."
            "test_delivery_failure_requeues_without_ledger_or_paid_state_regression"
        ),
    ),
    Mutant(
        mutation_class="paid_monotonicity",
        name="allow_paid_state_to_handle_late_events",
        path="reservation_followup/payment.py",
        old="    PaymentStatus.PAID: (PaymentEventAction.TERMINAL_NOOP,) * 8,\n",
        new="    PaymentStatus.PAID: (PaymentEventAction.HANDLE,) * 8,\n",
        test=(
            "tests.test_phase6_payment_reducer.Phase6PaymentReducerTests."
            "test_paid_state_is_monotonic_and_finished_replay_is_idempotent"
        ),
    ),
    Mutant(
        mutation_class="config_closure",
        name="allow_required_handoff_email_config",
        path="reservation_followup/types.py",
        old=(
            "                EffectRequirement.OPTIONAL,\n"
            "                EffectRequirement.DISABLED,\n"
            "            )\n"
            "        ):\n"
            "            raise ValueError(\"internal_email must be optional or disabled\")\n"
        ),
        new=(
            "                EffectRequirement.OPTIONAL,\n"
            "                EffectRequirement.DISABLED,\n"
            "                EffectRequirement.REQUIRED,\n"
            "            )\n"
            "        ):\n"
            "            raise ValueError(\"internal_email must be optional or disabled\")\n"
        ),
        test=(
            "tests.test_phase6_types.Phase6SharedTypeTests."
            "test_handoff_policy_internal_email_is_only_optional_or_disabled"
        ),
    ),
    Mutant(
        mutation_class="divergent_replay",
        name="accept_divergent_payment_event_replay",
        path="reservation_followup/payment.py",
        old='            raise ValueError("payment event id replay has divergent payload")\n',
        new="            return _noop(state)\n",
        test=(
            "tests.test_phase6_payment_reducer.Phase6PaymentReducerTests."
            "test_same_event_id_with_divergent_payload_conflicts_before_state_change"
        ),
    ),
)


class _DuplicateJsonKey(ValueError):
    pass


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _classify_test_run(
    process: _ProcessResult,
    *,
    baseline: bool = False,
) -> _ClassifiedRun:
    if type(process) is not _ProcessResult:
        raise TypeError("process must be exact _ProcessResult")
    if process.timed_out:
        return _ClassifiedRun("timeout", False, False, 0, 0, 0, "test timed out")
    protocol = [
        line.removeprefix(_TEST_RESULT_PREFIX)
        for line in process.stdout.splitlines()
        if line.startswith(_TEST_RESULT_PREFIX)
    ]
    if len(protocol) != 1:
        return _ClassifiedRun(
            "invalid_protocol",
            False,
            False,
            0,
            0,
            0,
            "test result protocol must appear exactly once",
        )
    try:
        payload = json.loads(
            protocol[0],
            object_pairs_hook=_json_object_without_duplicates,
        )
    except _DuplicateJsonKey as exc:
        return _ClassifiedRun(
            "invalid_protocol", False, False, 0, 0, 0, str(exc)
        )
    except json.JSONDecodeError:
        return _ClassifiedRun(
            "invalid_protocol", False, False, 0, 0, 0, "test result protocol is invalid JSON"
        )
    expected = {
        "loader_error",
        "tests_run",
        "failures",
        "errors",
        "error_types",
        "successful",
    }
    if type(payload) is not dict or set(payload) != expected:
        return _ClassifiedRun(
            "invalid_protocol", False, False, 0, 0, 0, "test result schema is invalid"
        )
    loader_error = payload["loader_error"]
    successful = payload["successful"]
    tests_run = payload["tests_run"]
    failures = payload["failures"]
    errors = payload["errors"]
    error_types = payload["error_types"]
    if (
        type(loader_error) is not bool
        or type(successful) is not bool
        or any(type(value) is not int or value < 0 for value in (tests_run, failures, errors))
        or type(error_types) is not list
        or any(type(value) is not str or not value for value in error_types)
        or len(error_types) != errors
    ):
        return _ClassifiedRun(
            "invalid_protocol", False, False, 0, 0, 0, "test result types are invalid"
        )
    if loader_error:
        return _ClassifiedRun(
            "loader_error", False, True, tests_run, failures, errors, "test failed to load"
        )
    infrastructure_errors = tuple(
        error_type
        for error_type in error_types
        if error_type in _NON_KILLING_ERROR_TYPES
    )
    if infrastructure_errors:
        return _ClassifiedRun(
            "infrastructure_error",
            False,
            False,
            tests_run,
            failures,
            errors,
            "test raised non-killing infrastructure errors: "
            + ", ".join(infrastructure_errors),
        )
    if baseline:
        if process.exit_code == 0 and successful and tests_run > 0 and failures == errors == 0:
            return _ClassifiedRun(
                "baseline_green", False, False, tests_run, failures, errors, None
            )
        return _ClassifiedRun(
            "baseline_failure",
            False,
            False,
            tests_run,
            failures,
            errors,
            "mutant test does not pass on the unmodified tree",
        )
    if (
        process.exit_code > 0
        and not successful
        and tests_run > 0
        and failures + errors > 0
    ):
        return _ClassifiedRun(
            "test_failure", True, False, tests_run, failures, errors, None
        )
    return _ClassifiedRun(
        "survived",
        False,
        False,
        tests_run,
        failures,
        errors,
        "mutant survived its exact target test",
    )


def _run_test(*, root: Path, test: str, timeout_seconds: int = 120) -> tuple[_ProcessResult, _ClassifiedRun]:
    try:
        completed = subprocess.run(
            [sys.executable, "-B", "-c", _STRUCTURED_UNITTEST_RUNNER, test],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        process = _ProcessResult(completed.returncode, completed.stdout, False)
    except subprocess.TimeoutExpired as exc:
        process = _ProcessResult(-2, exc.stdout or "", True)
    return process, _classify_test_run(process)


def _replace_target(*, root: Path, mutant: Mutant) -> bytes:
    if not isinstance(root, Path):
        raise TypeError("root must be a pathlib.Path")
    if type(mutant) is not Mutant:
        raise TypeError("mutant must be exact Mutant")
    target = root / mutant.path
    original = target.read_bytes()
    source = original.decode("utf-8")
    target_count = source.count(mutant.old)
    if target_count != 1:
        raise ValueError(f"mutation target count was {target_count}, expected 1")
    target.write_text(source.replace(mutant.old, mutant.new, 1), encoding="utf-8")
    return original


def _copy_scope(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for name in (
        "reservation_domain",
        "reservation_execution",
        "reservation_followup",
        "schemas",
        "scripts",
        "tests",
    ):
        origin = source / name
        if origin.exists():
            shutil.copytree(
                origin,
                destination / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.db", "*.sqlite", "*.sqlite3", "*-wal", "*-shm"),
            )


def _run_one(
    *,
    copy_root: Path,
    mutant: Mutant,
    baseline: _ClassifiedRun,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mutation_class": mutant.mutation_class,
        "name": mutant.name,
        "path": mutant.path,
        "test": mutant.test,
        "target_count": 0,
        "baseline_exit_code": 0 if baseline.verdict == "baseline_green" else 1,
        "exit_code": -1,
        "loader_error": baseline.loader_error,
        "killed": False,
        "error": baseline.error,
    }
    if baseline.verdict != "baseline_green":
        return result
    target = copy_root / mutant.path
    original: bytes | None = None
    try:
        original = _replace_target(root=copy_root, mutant=mutant)
        result["target_count"] = 1
        process, classified = _run_test(root=copy_root, test=mutant.test)
        result.update(
            {
                "exit_code": process.exit_code,
                "loader_error": classified.loader_error,
                "killed": classified.killed,
                "error": classified.error,
                "tests_run": classified.tests_run,
                "failures": classified.failures,
                "errors": classified.errors,
            }
        )
        return result
    except (OSError, UnicodeError, ValueError) as exc:
        result["error"] = str(exc)
        return result
    finally:
        if original is not None:
            target.write_bytes(original)


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
    if tuple(mutant.mutation_class for mutant in MUTANTS) != MUTANT_CLASSES:
        raise ValueError("mutation class catalog is not exact")
    if selected_names is None:
        selected = MUTANTS
    else:
        if type(selected_names) is not tuple or any(type(name) is not str for name in selected_names):
            raise TypeError("selected_names must be a tuple of strings")
        unknown = sorted(set(selected_names) - set(catalog))
        if unknown:
            raise ValueError(f"unknown mutants: {unknown}")
        selected = tuple(catalog[name] for name in selected_names)
    if not selected:
        raise ValueError("at least one mutant must be selected")

    baseline_cache: dict[str, _ClassifiedRun] = {}
    for mutant in selected:
        if mutant.test in baseline_cache:
            continue
        process, _ = _run_test(root=root, test=mutant.test)
        baseline_cache[mutant.test] = _classify_test_run(process, baseline=True)

    with tempfile.TemporaryDirectory(prefix="phase6-mutation-catalog-") as directory:
        copy_root = Path(directory) / "repo"
        _copy_scope(root, copy_root)
        results = [
            _run_one(
                copy_root=copy_root,
                mutant=mutant,
                baseline=baseline_cache[mutant.test],
            )
            for mutant in selected
        ]
    return {
        "schema_version": 1,
        "phase": PHASE,
        "scope": "one disposable repository copy; exact file bytes restored after each mutant",
        "catalog_count": len(MUTANTS),
        "mutant_count": len(results),
        "baseline_runs": len(baseline_cache),
        "all_killed": bool(results) and all(result["killed"] for result in results),
        "mutants": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args(argv)
    try:
        selected = tuple(args.only) if args.only else None
        report = run_mutants(root=ROOT, selected_names=selected)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["all_killed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
