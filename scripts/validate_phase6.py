#!/usr/bin/env python3
"""Closed independent validator for Phase 6 handoff/payment evidence and purity."""

from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_followup.schema import (  # noqa: E402
    SCHEMA_VERSION,
    render_postgresql,
    render_sqlite,
    schema_contract,
)
from scripts.generate_phase6_manifest import (  # noqa: E402
    build_package_manifest,
    build_schema_manifest,
    check_manifests,
    checksum_paths,
    render_sums,
)
from scripts.validate_phase0 import check_markdown_links, check_secrets_and_pii  # noqa: E402

PHASE = "phase-06-handoff-and-payments"
SEED = 2_026_071_906
MINIMUM_PROPERTY_CASES = 20_000
MINIMUM_RESTART_SCHEDULES = 2_000
MINIMUM_CONTENTION_ROUNDS = 50
EVIDENCE_RELATIVE = Path("docs/refactor/evidence/phase-06")
EVIDENCE = ROOT / EVIDENCE_RELATIVE
PACKAGE_RELATIVE = Path("reservation_followup")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")

PROPERTY_MODES = (
    "handoff_pre_email_disabled",
    "payment_pix_method_selected",
    "handoff_post_success",
    "payment_wise_method_switch",
    "handoff_manual_review",
    "payment_stripe_economic_change",
    "handoff_optional_email_failure",
    "payment_pix_evidence_conflict",
    "handoff_pre_email_disabled_replay",
    "payment_wise_pre_fence_recovery",
    "handoff_post_success_replay",
    "payment_stripe_post_fence_manual_review",
    "handoff_manual_review_replay",
    "payment_pix_optional_effect_failure",
    "handoff_optional_email_failure_replay",
    "payment_wise_method_selected_repeat",
)
POSITIVE_KEYS = (
    "economic_version_changes",
    "email_disabled_cases",
    "evidence_conflicts",
    "handoff_cases",
    "method_switches",
    "optional_effect_failures",
    "payment_cases",
    "pix_cases",
    "post_fence_manual_reviews",
    "pre_fence_recoveries",
    "required_effect_deliveries",
    "stripe_cases",
    "wise_cases",
)
SAFETY_KEYS = (
    "handoff_email_failures_do_not_block_required",
    "outbox_settlement_retries",
    "partial_transactions",
    "proof_reuses",
    "reservation_commands_after_anchor",
    "second_dispatch_slots",
    "second_settlement_commands",
    "unknown_automatic_retries",
    "wrong_target_settlements",
)
_MODE_CONTRACT = {
    "handoff_pre_email_disabled": ("handoff", None, None, None, False, {"email_disabled_cases": 1, "handoff_cases": 1}),
    "payment_pix_method_selected": ("payment", "lodging", "hostel", "pix", True, {"payment_cases": 1, "pix_cases": 1}),
    "handoff_post_success": ("handoff", "activity", "agency", None, True, {"email_disabled_cases": 1, "handoff_cases": 1, "required_effect_deliveries": 1}),
    "payment_wise_method_switch": ("payment", "activity", "agency", "wise", True, {"method_switches": 1, "payment_cases": 1, "wise_cases": 1}),
    "handoff_manual_review": ("handoff", "lodging", "hostel", None, True, {"email_disabled_cases": 1, "handoff_cases": 1}),
    "payment_stripe_economic_change": ("payment", "lodging", "hostel", "stripe", True, {"economic_version_changes": 1, "payment_cases": 1, "stripe_cases": 1}),
    "handoff_optional_email_failure": ("handoff", None, None, None, False, {"handoff_cases": 1, "optional_effect_failures": 1, "required_effect_deliveries": 1}),
    "payment_pix_evidence_conflict": ("payment", "activity", "agency", "pix", True, {"evidence_conflicts": 1, "payment_cases": 1, "pix_cases": 1}),
    "handoff_pre_email_disabled_replay": ("handoff", None, None, None, False, {"email_disabled_cases": 1, "handoff_cases": 1}),
    "payment_wise_pre_fence_recovery": ("payment", "lodging", "hostel", "wise", True, {"payment_cases": 1, "pre_fence_recoveries": 1, "wise_cases": 1}),
    "handoff_post_success_replay": ("handoff", "activity", "agency", None, True, {"email_disabled_cases": 1, "handoff_cases": 1}),
    "payment_stripe_post_fence_manual_review": ("payment", "activity", "agency", "stripe", True, {"payment_cases": 1, "post_fence_manual_reviews": 1, "required_effect_deliveries": 1, "stripe_cases": 1}),
    "handoff_manual_review_replay": ("handoff", "lodging", "hostel", None, True, {"email_disabled_cases": 1, "handoff_cases": 1}),
    "payment_pix_optional_effect_failure": ("payment", "lodging", "hostel", "pix", True, {"optional_effect_failures": 1, "payment_cases": 1, "pix_cases": 1, "required_effect_deliveries": 2}),
    "handoff_optional_email_failure_replay": ("handoff", None, None, None, False, {"handoff_cases": 1, "optional_effect_failures": 1, "required_effect_deliveries": 1}),
    "payment_wise_method_selected_repeat": ("payment", "activity", "agency", "wise", True, {"payment_cases": 1, "wise_cases": 1}),
}
_EXPECTED_COUNTERS = {
    "economic_version_changes": 1250,
    "email_disabled_cases": 7500,
    "evidence_conflicts": 1250,
    "handoff_cases": 10000,
    "handoff_email_failures_do_not_block_required": 0,
    "method_switches": 1250,
    "optional_effect_failures": 3750,
    "outbox_settlement_retries": 0,
    "partial_transactions": 0,
    "payment_cases": 10000,
    "pix_cases": 3750,
    "post_fence_manual_reviews": 1250,
    "pre_fence_recoveries": 1250,
    "proof_reuses": 0,
    "required_effect_deliveries": 7500,
    "reservation_commands_after_anchor": 0,
    "second_dispatch_slots": 0,
    "second_settlement_commands": 0,
    "stripe_cases": 2500,
    "unknown_automatic_retries": 0,
    "wise_cases": 3750,
    "wrong_target_settlements": 0,
}
_EXPECTED_MODE_COUNTS = {mode: 1250 for mode in PROPERTY_MODES}

FAULT_POINTS = (
    "handoff_before_event",
    "handoff_after_event_before_state",
    "handoff_after_state_before_required_outbox",
    "handoff_after_required_outbox_before_optional_outbox",
    "handoff_after_optional_outbox_before_commit",
    "handoff_after_commit_before_claim",
    "handoff_during_delivery",
    "handoff_after_delivery_before_receipt",
    "payment_before_anchor",
    "payment_after_anchor_before_state",
    "payment_after_state_before_event",
    "payment_after_event_before_commit",
    "payment_before_evidence_claim",
    "payment_after_evidence_before_command",
    "payment_after_command_before_ledger",
    "payment_after_ledger_before_commit",
    "settlement_after_claim_before_prepare",
    "settlement_after_prepare_before_fence",
    "settlement_after_fence_before_dispatch",
    "settlement_during_dispatch",
    "settlement_after_dispatch_before_outcome",
    "settlement_after_outcome_before_state",
    "settlement_after_state_before_outboxes",
    "settlement_after_outboxes_before_commit",
    "payment_effect_after_commit_before_claim",
    "payment_effect_during_delivery",
    "payment_effect_after_delivery_before_receipt",
)
RESTART_POINTS = (
    "handoff_after_commit_before_claim",
    "handoff_during_delivery",
    "handoff_after_delivery_before_receipt",
    "payment_before_evidence_claim",
    "settlement_after_claim_before_prepare",
    "settlement_after_prepare_before_fence",
    "settlement_after_fence_before_dispatch",
    "settlement_during_dispatch",
    "settlement_after_dispatch_before_outcome",
    "payment_effect_after_commit_before_claim",
    "payment_effect_during_delivery",
    "payment_effect_after_delivery_before_receipt",
)
_RESTART_CYCLE = (
    "settlement_after_fence_before_dispatch",
    "settlement_during_dispatch",
    "settlement_after_prepare_before_fence",
    "handoff_during_delivery",
    "payment_effect_after_delivery_before_receipt",
    "handoff_after_delivery_before_receipt",
    "payment_effect_during_delivery",
    "handoff_after_commit_before_claim",
    "settlement_after_dispatch_before_outcome",
    "payment_before_evidence_claim",
    "payment_effect_after_commit_before_claim",
    "settlement_after_claim_before_prepare",
)
CONTENTION_DOMAINS = (
    "handoff_incident",
    "payment_command",
    "global_evidence_claim",
    "payment_outbox",
)

MUTATION_CATALOG = (
    {"mutation_class": "handoff_policy", "name": "email_disabled_blocks_required_handoff", "path": "reservation_followup/handoff.py", "test": "tests.test_phase6_handoff.Phase6HandoffReducerTests.test_email_disabled_still_opens_queue_and_customer_ack"},
    {"mutation_class": "handoff_precedence", "name": "resurface_stale_followup_after_handoff", "path": "reservation_followup/handoff.py", "test": "tests.test_phase6_handoff.Phase6HandoffProjectionTests.test_terminal_handoff_suppresses_stale_confirmation_and_missing_slots"},
    {"mutation_class": "payment_bootstrap", "name": "accept_nonconfirmed_reservation_outcome", "path": "reservation_followup/types.py", "test": "tests.test_phase6_payment.Phase6PaymentEvidenceTests.test_only_effect_confirmed_anchor_can_bootstrap_payment"},
    {"mutation_class": "method_separation", "name": "treat_wise_and_stripe_as_pix", "path": "reservation_followup/payment.py", "test": "tests.test_phase6_payment.Phase6PaymentEvidenceTests.test_method_profiles_come_from_exact_trusted_configuration"},
    {"mutation_class": "global_claim", "name": "remove_global_payment_evidence_claim", "path": "reservation_followup/sqlite_store.py", "test": "tests.test_phase6_payment_claims.Phase6PaymentClaimTests.test_pix_claim_is_global_across_target_business_unit_and_caller_keys"},
    {"mutation_class": "amount_receiver_validation", "name": "accept_divergent_pix_economics_and_receiver", "path": "reservation_followup/payment.py", "test": "tests.test_phase6_payment.Phase6PaymentEvidenceTests.test_pix_rejects_mismatch_pending_placeholder_entropy_and_hash"},
    {"mutation_class": "dispatch_slot", "name": "allow_second_dispatch_slot", "path": "reservation_followup/schema.py", "test": "tests.test_phase6_schema.Phase6SchemaTests.test_render_is_deterministic_tracked_and_contains_only_create_tables"},
    {"mutation_class": "post_fence_retry", "name": "recover_post_fence_as_retryable", "path": "reservation_followup/reconciliation.py", "test": "tests.test_phase6_reconciliation.Phase6PaymentReconciliationTests.test_post_fence_recovery_is_one_shot_and_never_changes_slot_or_calls_port"},
    {"mutation_class": "outbox_isolation", "name": "payment_outbox_rewrites_settlement_ledger", "path": "reservation_followup/workers.py", "test": "tests.test_phase6_payment_outbox.Phase6PaymentOutboxTests.test_delivery_failure_requeues_without_ledger_or_paid_state_regression"},
    {"mutation_class": "paid_monotonicity", "name": "allow_paid_state_to_handle_late_events", "path": "reservation_followup/payment.py", "test": "tests.test_phase6_payment_reducer.Phase6PaymentReducerTests.test_paid_state_is_monotonic_and_finished_replay_is_idempotent"},
    {"mutation_class": "config_closure", "name": "allow_required_handoff_email_config", "path": "reservation_followup/types.py", "test": "tests.test_phase6_types.Phase6SharedTypeTests.test_handoff_policy_internal_email_is_only_optional_or_disabled"},
    {"mutation_class": "divergent_replay", "name": "accept_divergent_payment_event_replay", "path": "reservation_followup/payment.py", "test": "tests.test_phase6_payment_reducer.Phase6PaymentReducerTests.test_same_event_id_with_divergent_payload_conflicts_before_state_change"},
)

REQUIRED = (
    "README.md",
    "docs/refactor/README.md",
    "docs/refactor/evidence/README.md",
    "docs/refactor/06-risk-register.md",
    "docs/refactor/phases/phase-06-handoff-and-payments.md",
    "docs/superpowers/specs/2026-07-19-phase-6-handoff-payments-design.md",
    "docs/superpowers/plans/2026-07-19-phase-6-handoff-payments.md",
    ".github/workflows/phase6.yml",
    "schemas/phase6/sqlite.sql",
    "schemas/phase6/postgresql.sql",
    "scripts/generate_phase6_schema.py",
    "scripts/generate_phase6_manifest.py",
    "scripts/run_phase6_properties.py",
    "scripts/run_phase6_faults.py",
    "scripts/run_phase6_mutations.py",
    "scripts/validate_phase6.py",
    "tests/phase6_helpers.py",
    "tests/test_phase6_closeout.py",
    "tests/test_phase6_properties.py",
    "docs/refactor/evidence/phase-06/README.md",
    "docs/refactor/evidence/phase-06/entry-baseline.json",
    "docs/refactor/evidence/phase-06/property-result.json",
    "docs/refactor/evidence/phase-06/fault-matrix.json",
    "docs/refactor/evidence/phase-06/restart-result.json",
    "docs/refactor/evidence/phase-06/concurrency-result.json",
    "docs/refactor/evidence/phase-06/mutation-result.json",
    "docs/refactor/evidence/phase-06/schema-manifest.json",
    "docs/refactor/evidence/phase-06/package-manifest.json",
    "docs/refactor/evidence/phase-06/validation-result.json",
    "docs/refactor/evidence/phase-06/performance-result.json",
    "docs/refactor/evidence/phase-06/ci-result.json",
    "docs/refactor/evidence/phase-06/adversarial-review.md",
    "docs/refactor/evidence/phase-06/task14-red.patch",
    "docs/refactor/evidence/phase-06/task14-budget-red.patch",
    "docs/refactor/evidence/phase-06/task14-process-red.patch",
    "docs/refactor/evidence/phase-06/task14-purity-matrix.patch",
    "docs/refactor/evidence/phase-06/red-result-closeout.json",
    "docs/refactor/evidence/phase-06/SHA256SUMS",
)
FORBIDDEN_IMPORTS = {
    "aiohttp", "anthropic", "boto3", "fastapi", "http", "httpx", "openai",
    "psycopg", "psycopg2", "redis", "requests", "socket", "sqlalchemy",
    "subprocess", "multiprocessing",
    "smtplib", "stripe", "supabase", "urllib",
}
_RUNTIME_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pyo"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
    )
    if type(payload) is not dict:
        raise ValueError("JSON root must be an object")
    return payload


def _exact_int(value: object, expected: int | None = None) -> bool:
    return type(value) is int and (expected is None or value == expected)


def _exact_float(value: object) -> bool:
    return type(value) is float and value >= 0.0


def _exact_json(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return set(actual) == set(expected) and all(
            _exact_json(actual[key], value) for key, value in expected.items()
        )
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _exact_json(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _zero_map(keys: tuple[str, ...]) -> dict[str, int]:
    return {key: 0 for key in keys}


def check_property_payload(failures: list[str], payload: dict[str, Any]) -> None:
    expected_top = {"configuration", "mode", "phase", "report", "result", "schema_version"}
    if type(payload) is not dict or set(payload) != expected_top:
        failures.append("property envelope keys mismatch")
        return
    configuration = payload.get("configuration")
    if not _exact_json(
        configuration,
        {"cases": 20_000, "minimum_gate_cases": 20_000, "seed": SEED},
    ):
        failures.append("property configuration mismatch")
    if (
        payload.get("schema_version") != 1
        or type(payload.get("schema_version")) is not int
        or payload.get("phase") != PHASE
        or payload.get("mode") != "gate"
        or payload.get("result") != "passed"
    ):
        failures.append("property envelope identity mismatch")
    report = payload.get("report")
    report_keys = {
        "audits", "business_unit_counts", "cases", "counters", "method_counts",
        "mode_counts", "passed", "payment_dimension_counts", "rows", "seed",
        "service_counts", "start", "violations",
    }
    if type(report) is not dict or set(report) != report_keys:
        failures.append("property report keys mismatch")
        return
    if not _exact_int(report.get("cases"), 20_000) or not _exact_int(report.get("seed"), SEED) or not _exact_int(report.get("start"), 0) or report.get("passed") is not True or report.get("violations") != []:
        failures.append("property report scalar mismatch")
    expected_aggregates = {
        "counters": _EXPECTED_COUNTERS,
        "mode_counts": _EXPECTED_MODE_COUNTS,
        "method_counts": {"pix": 3750, "stripe": 2500, "wise": 3750},
        "business_unit_counts": {"agency": 7500, "hostel": 7500},
        "service_counts": {"activity": 7500, "lodging": 7500},
        "payment_dimension_counts": {
            "pix|activity|agency": 1250,
            "pix|lodging|hostel": 2500,
            "stripe|activity|agency": 1250,
            "stripe|lodging|hostel": 1250,
            "wise|activity|agency": 2500,
            "wise|lodging|hostel": 1250,
        },
    }
    for key, expected in expected_aggregates.items():
        if not _exact_json(report.get(key), expected):
            failures.append(f"property {key} mismatch")
    rows = report.get("rows")
    row_keys = {
        "business_unit", "case_kind", "index", "mode", "payment_method",
        "positive", "reservation_command_id", "reservation_outcome_hash",
        "reservation_path_confirmed", "reservation_workflow_id", "safety", "service",
    }
    if type(rows) is not list or len(rows) != 20_000:
        failures.append("property rows cardinality mismatch")
    else:
        aggregate_positive = Counter()
        aggregate_safety = Counter()
        aggregate_modes = Counter()
        workflow_ids: set[str] = set()
        command_ids: set[str] = set()
        outcome_hashes: set[str] = set()
        for index, row in enumerate(rows):
            if type(row) is not dict or set(row) != row_keys:
                failures.append(f"property row schema mismatch at {index}")
                break
            mode = PROPERTY_MODES[index % len(PROPERTY_MODES)]
            contract = _MODE_CONTRACT[mode]
            expected_positive = _zero_map(POSITIVE_KEYS)
            expected_positive.update(contract[5])
            if (
                not _exact_int(row.get("index"), index)
                or row.get("mode") != mode
                or row.get("case_kind") != contract[0]
                or row.get("service") != contract[1]
                or row.get("business_unit") != contract[2]
                or row.get("payment_method") != contract[3]
                or row.get("reservation_path_confirmed") is not contract[4]
                or not _exact_json(row.get("positive"), expected_positive)
                or not _exact_json(row.get("safety"), _zero_map(SAFETY_KEYS))
            ):
                failures.append(f"property row contract mismatch at {index}")
                break
            identity = (
                row.get("reservation_workflow_id"),
                row.get("reservation_command_id"),
                row.get("reservation_outcome_hash"),
            )
            if contract[4]:
                valid_identity = (
                    type(identity[0]) is str
                    and re.fullmatch(r"workflow:[a-f0-9]{64}", identity[0]) is not None
                    and type(identity[1]) is str
                    and re.fullmatch(r"cmd:[a-f0-9]{32}", identity[1]) is not None
                    and type(identity[2]) is str
                    and HASH_RE.fullmatch(identity[2]) is not None
                )
                if not valid_identity:
                    failures.append(f"property confirmed identity mismatch at {index}")
                    break
                if (
                    identity[0] in workflow_ids
                    or identity[1] in command_ids
                    or identity[2] in outcome_hashes
                ):
                    failures.append(f"duplicate property confirmed identity at {index}")
                    break
                workflow_ids.add(identity[0])
                command_ids.add(identity[1])
                outcome_hashes.add(identity[2])
            elif identity != (None, None, None):
                failures.append(f"property pre-anchor identity mismatch at {index}")
                break
            aggregate_positive.update(row["positive"])
            aggregate_safety.update(row["safety"])
            aggregate_modes[mode] += 1
        else:
            if not (
                len(workflow_ids) == len(command_ids) == len(outcome_hashes) == 15_000
            ):
                failures.append("property confirmed identity cardinality mismatch")
            combined = dict(aggregate_positive)
            combined.update(aggregate_safety)
            if combined != _EXPECTED_COUNTERS or dict(aggregate_modes) != _EXPECTED_MODE_COUNTS:
                failures.append("property aggregates do not reconstruct from rows")
    audits = report.get("audits")
    if type(audits) is not list or len(audits) != 20:
        failures.append("property audit cardinality mismatch")
    else:
        audit_keys = {"cases", "deep_audits", "foreign_key_violations", "quick_check", "start"}
        for index, row in enumerate(audits):
            start = index * 1000
            expected_deep = 63 if start in {0, 16000} else 0
            if type(row) is not dict or set(row) != audit_keys or not _exact_json(
                row,
                {"cases": 1000, "deep_audits": expected_deep, "foreign_key_violations": 0, "quick_check": "ok", "start": start},
            ):
                failures.append(f"property audit mismatch at {index}")
                break


_FAULT_ROW_KEYS = {
    "child_exit_code", "delivery_calls_after_child", "delivery_calls_during_recovery",
    "delivery_calls_final", "delivery_calls_setup_baseline", "dispatch_slots",
    "evidence_owners", "fault_point", "final_handoff_outbox_status",
    "final_payment_outbox_status", "final_settlement_status", "handoff_receipts",
    "mechanism", "partial_transactions", "payment_receipts",
    "provider_calls_after_child", "provider_calls_during_recovery",
    "provider_calls_final", "provider_calls_setup_baseline", "rollback_verified",
    "schedule", "settlement_commands", "unknown_automatic_retries", "violations",
}


def _expected_fault_row(point: str, schedule: int) -> dict[str, Any]:
    row: dict[str, Any] = {
        "child_exit_code": None,
        "delivery_calls_after_child": 0,
        "delivery_calls_during_recovery": 0,
        "delivery_calls_final": 0,
        "delivery_calls_setup_baseline": 0,
        "dispatch_slots": 0,
        "evidence_owners": 0,
        "fault_point": point,
        "final_handoff_outbox_status": None,
        "final_payment_outbox_status": None,
        "final_settlement_status": None,
        "handoff_receipts": 0,
        "mechanism": "transaction_trigger",
        "partial_transactions": 0,
        "payment_receipts": 0,
        "provider_calls_after_child": 0,
        "provider_calls_during_recovery": 0,
        "provider_calls_final": 0,
        "provider_calls_setup_baseline": 0,
        "rollback_verified": True,
        "schedule": schedule,
        "settlement_commands": 0,
        "unknown_automatic_retries": 0,
        "violations": [],
    }
    if point in RESTART_POINTS:
        row.update({"child_exit_code": 91, "mechanism": "process_crash", "rollback_verified": None})
    if point == "handoff_after_commit_before_claim":
        row.update({"delivery_calls_during_recovery": 1, "delivery_calls_final": 1, "handoff_receipts": 1, "final_handoff_outbox_status": "delivered"})
    elif point in {"handoff_during_delivery", "handoff_after_delivery_before_receipt"}:
        row.update({"delivery_calls_after_child": 1, "delivery_calls_during_recovery": 1, "delivery_calls_final": 2, "handoff_receipts": 1, "final_handoff_outbox_status": "delivered"})
    elif point == "payment_before_evidence_claim":
        row.update({"settlement_commands": 1, "evidence_owners": 1, "final_settlement_status": "queued"})
    elif point in {"settlement_after_claim_before_prepare", "settlement_after_prepare_before_fence"}:
        row.update({"provider_calls_during_recovery": 1, "provider_calls_final": 1, "settlement_commands": 1, "evidence_owners": 1, "dispatch_slots": 1, "final_settlement_status": "outcome_recorded", "final_payment_outbox_status": "pending"})
    elif point == "settlement_after_fence_before_dispatch":
        row.update({"settlement_commands": 1, "evidence_owners": 1, "dispatch_slots": 1, "final_settlement_status": "manual_review", "final_payment_outbox_status": "pending"})
    elif point in {"settlement_during_dispatch", "settlement_after_dispatch_before_outcome"}:
        row.update({"provider_calls_after_child": 1, "provider_calls_final": 1, "settlement_commands": 1, "evidence_owners": 1, "dispatch_slots": 1, "final_settlement_status": "manual_review", "final_payment_outbox_status": "pending"})
    elif point in {"settlement_after_outcome_before_state", "settlement_after_state_before_outboxes", "settlement_after_outboxes_before_commit"}:
        row.update({"provider_calls_after_child": 1, "provider_calls_final": 1, "settlement_commands": 1, "evidence_owners": 1, "dispatch_slots": 1, "final_settlement_status": "manual_review", "final_payment_outbox_status": "pending"})
    elif point == "payment_effect_after_commit_before_claim":
        row.update({"provider_calls_setup_baseline": 1, "provider_calls_after_child": 1, "provider_calls_final": 1, "delivery_calls_during_recovery": 1, "delivery_calls_final": 1, "settlement_commands": 1, "evidence_owners": 1, "dispatch_slots": 1, "payment_receipts": 1, "final_settlement_status": "outcome_recorded", "final_payment_outbox_status": "delivered"})
    elif point in {"payment_effect_during_delivery", "payment_effect_after_delivery_before_receipt"}:
        row.update({"provider_calls_setup_baseline": 1, "provider_calls_after_child": 1, "provider_calls_final": 1, "delivery_calls_after_child": 1, "delivery_calls_during_recovery": 1, "delivery_calls_final": 2, "settlement_commands": 1, "evidence_owners": 1, "dispatch_slots": 1, "payment_receipts": 1, "final_settlement_status": "outcome_recorded", "final_payment_outbox_status": "delivered"})
    return row


def check_fault_payloads(
    failures: list[str],
    fault: dict[str, Any],
    restart: dict[str, Any],
    concurrency: dict[str, Any],
) -> None:
    expected_fault_top = {"configuration", "fault_points", "kind", "phase", "result", "schedules", "schema_version", "violations"}
    if type(fault) is not dict or set(fault) != expected_fault_top or not _exact_json(fault.get("configuration"), {"fault_point_count": 27, "seed": SEED}) or fault.get("fault_points") != list(FAULT_POINTS) or fault.get("kind") != "fault-matrix" or fault.get("phase") != PHASE or fault.get("result") != "passed" or not _exact_int(fault.get("schema_version"), 1) or not _exact_int(fault.get("violations"), 0):
        failures.append("fault matrix envelope mismatch")
    schedules = fault.get("schedules")
    if type(schedules) is not list or len(schedules) != 27:
        failures.append("fault matrix cardinality mismatch")
    else:
        for index, (point, row) in enumerate(zip(FAULT_POINTS, schedules)):
            if type(row) is not dict or set(row) != _FAULT_ROW_KEYS or not _exact_json(row, _expected_fault_row(point, index)):
                failures.append(f"fault schedule mismatch at {index}")
                break

    expected_restart_top = {"configuration", "fault_point_counts", "fault_points", "kind", "phase", "result", "schedules", "schema_version", "violations"}
    if type(restart) is not dict or set(restart) != expected_restart_top or not _exact_json(restart.get("configuration"), {"schedules": 2000, "seed": SEED}) or restart.get("fault_points") != list(RESTART_POINTS) or restart.get("kind") != "restart-schedules" or restart.get("phase") != PHASE or restart.get("result") != "passed" or not _exact_int(restart.get("schema_version"), 1) or not _exact_int(restart.get("violations"), 0):
        failures.append("restart envelope mismatch")
    restart_rows = restart.get("schedules")
    if type(restart_rows) is not list or len(restart_rows) != 2000:
        failures.append("restart cardinality mismatch")
    else:
        counts = Counter()
        for index, row in enumerate(restart_rows):
            point = _RESTART_CYCLE[index % len(_RESTART_CYCLE)]
            if type(row) is not dict or set(row) != _FAULT_ROW_KEYS or not _exact_json(row, _expected_fault_row(point, index)):
                failures.append(f"restart schedule mismatch at {index}")
                break
            counts[point] += 1
        if not _exact_json(restart.get("fault_point_counts"), dict(sorted(counts.items()))):
            failures.append("restart counts do not reconstruct")

    expected_contention_top = {"configuration", "domain_rounds", "domain_winners", "domains", "kind", "phase", "result", "round_results", "schema_version", "violations"}
    if type(concurrency) is not dict or set(concurrency) != expected_contention_top or not _exact_json(concurrency.get("configuration"), {"rounds": 50, "seed": SEED}) or concurrency.get("domains") != list(CONTENTION_DOMAINS) or concurrency.get("kind") != "multiprocess-contention" or concurrency.get("phase") != PHASE or concurrency.get("result") != "passed" or not _exact_int(concurrency.get("schema_version"), 1) or not _exact_int(concurrency.get("violations"), 0):
        failures.append("contention envelope mismatch")
    rows = concurrency.get("round_results")
    row_keys = {"child_error_types", "child_errors", "domain", "durable_owners", "durable_tokens", "durable_winners", "nonzero_child_exits", "partial_transactions", "provider_calls_baseline", "provider_calls_final", "provider_delta", "round", "violations", "winners", "winning_owners", "winning_tokens"}
    if type(rows) is not list or len(rows) != 200:
        failures.append("contention cardinality mismatch")
    else:
        rounds = Counter()
        winners = Counter()
        handoff_owners: set[str] = set()
        global_claim_owners: set[str] = set()
        for index, row in enumerate(rows):
            round_index, domain_index = divmod(index, 4)
            domain = CONTENTION_DOMAINS[domain_index]
            provider_baseline = 1 if domain == "payment_outbox" else 0
            owner = None
            if type(row) is dict and type(row.get("winning_owners")) is list and len(row["winning_owners"]) == 1:
                owner = row["winning_owners"][0]
            owner_valid = (
                (domain == "handoff_incident" and type(owner) is str and re.fullmatch(r"contended-incident:[a-f0-9]{64}", owner) is not None)
                or (domain == "payment_command" and owner == "settlement:contender")
                or (domain == "global_evidence_claim" and type(owner) is str and re.fullmatch(r"pix:E1234567820290101ABCDEF[A-F0-9]{5}", owner) is not None)
                or (domain == "payment_outbox" and owner == "payment-claim:dfaf4c35eb49c452a34b2b4c9a6a8147af04d3116b58dcd8f391177ca0646e5d")
            )
            valid = (
                type(row) is dict
                and set(row) == row_keys
                and row.get("domain") == domain
                and _exact_int(row.get("round"), round_index)
                and _exact_int(row.get("winners"), 1)
                and _exact_int(row.get("durable_winners"), 1)
                and row.get("winning_tokens") == [1]
                and row.get("durable_tokens") == [1]
                and type(row.get("winning_owners")) is list
                and len(row["winning_owners"]) == 1
                and all(type(value) is str and value for value in row["winning_owners"])
                and row.get("durable_owners") == row.get("winning_owners")
                and owner_valid
                and _exact_int(row.get("child_errors"), 0)
                and row.get("child_error_types") == []
                and _exact_int(row.get("nonzero_child_exits"), 0)
                and _exact_int(row.get("partial_transactions"), 0)
                and _exact_int(row.get("provider_calls_baseline"), provider_baseline)
                and _exact_int(row.get("provider_calls_final"), provider_baseline)
                and _exact_int(row.get("provider_delta"), 0)
                and row.get("violations") == []
            )
            if not valid:
                failures.append(f"contention row mismatch at {index}")
                break
            rounds[domain] += 1
            winners[domain] += row["winners"]
            if domain == "handoff_incident":
                handoff_owners.add(owner)
            elif domain == "global_evidence_claim":
                global_claim_owners.add(owner)
        if len(handoff_owners) != 50 or len(global_claim_owners) != 50:
            failures.append("contention owner identity cardinality mismatch")
        if not _exact_json(concurrency.get("domain_rounds"), dict(rounds)) or not _exact_json(concurrency.get("domain_winners"), dict(winners)):
            failures.append("contention aggregates do not reconstruct")


def check_mutation_payload(failures: list[str], payload: dict[str, Any]) -> None:
    top_keys = {"all_killed", "baseline_runs", "catalog_count", "mutant_count", "mutants", "phase", "schema_version", "scope"}
    if type(payload) is not dict or set(payload) != top_keys or payload.get("phase") != PHASE or not _exact_int(payload.get("schema_version"), 1) or not _exact_int(payload.get("catalog_count"), 12) or not _exact_int(payload.get("mutant_count"), 12) or not _exact_int(payload.get("baseline_runs"), 12) or payload.get("all_killed") is not True or payload.get("scope") != "one disposable repository copy; exact file bytes restored after each mutant":
        failures.append("mutation envelope mismatch")
    rows = payload.get("mutants")
    row_keys = {"baseline_exit_code", "error", "errors", "exit_code", "failures", "killed", "loader_error", "mutation_class", "name", "path", "target_count", "test", "tests_run"}
    if type(rows) is not list or len(rows) != 12:
        failures.append("mutation cardinality mismatch")
        return
    seen = set()
    total_failures = 0
    total_errors = 0
    for index, (row, contract) in enumerate(zip(rows, MUTATION_CATALOG)):
        valid = (
            type(row) is dict
            and set(row) == row_keys
            and all(row.get(key) == value for key, value in contract.items())
            and _exact_int(row.get("baseline_exit_code"), 0)
            and type(row.get("exit_code")) is int
            and row["exit_code"] > 0
            and _exact_int(row.get("target_count"), 1)
            and type(row.get("tests_run")) is int
            and row["tests_run"] > 0
            and type(row.get("failures")) is int
            and row["failures"] >= 0
            and type(row.get("errors")) is int
            and row["errors"] >= 0
            and row["failures"] + row["errors"] > 0
            and row.get("killed") is True
            and row.get("loader_error") is False
            and row.get("error") is None
        )
        if not valid:
            failures.append(f"mutation row mismatch at {index}")
            break
        if row["name"] in seen:
            failures.append("duplicate mutation identity")
            break
        seen.add(row["name"])
        total_failures += row["failures"]
        total_errors += row["errors"]
        target = ROOT / row["path"]
        test_path = ROOT / (row["test"].split(".")[0] + "/" + row["test"].split(".")[1] + ".py")
        if not target.is_file() or not test_path.is_file():
            failures.append(f"mutation target/test missing at {index}")
            break
    if total_failures != 10 or total_errors != 7:
        failures.append("mutation failure/error totals diverge from the closed contract")


def _function_material(path: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = tuple(
            (
                child.func.id
                if isinstance(child.func, ast.Name)
                else child.func.attr
            )
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            and isinstance(child.func, (ast.Name, ast.Attribute))
        )
        statements = []
        for child in ast.walk(node):
            value = None
            if isinstance(child, (ast.Assign, ast.AnnAssign)):
                value = child.value
            elif (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in {"execute", "executemany", "executescript"}
                and child.args
            ):
                value = child.args[0]
            if value is None:
                continue
            material = " ".join(
                item.value
                for item in ast.walk(value)
                if isinstance(item, ast.Constant) and type(item.value) is str
            ).lower()
            if material:
                statements.append(material)
        result[node.name] = (tuple(statements), calls)
    return result


def check_package_purity(failures: list[str], *, root: Path = ROOT) -> dict[str, Any]:
    package = root / PACKAGE_RELATIVE
    paths = tuple(sorted(package.rglob("*.py"))) if package.is_dir() else ()
    external_imports = []
    environment_reads = []
    process_executions = []
    reconciler_capabilities = []
    outbox_ledger_references = []
    cross_workflow_writes = []
    for path in paths:
        relative = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"package syntax error: {relative}:{exc.lineno}")
            continue
        module_aliases: dict[str, str] = {}
        process_call_aliases: set[str] = set()
        for imported in ast.walk(tree):
            if isinstance(imported, ast.Import):
                for alias in imported.names:
                    module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(imported, ast.ImportFrom) and imported.module:
                module = imported.module
                for alias in imported.names:
                    local_name = alias.asname or alias.name
                    qualified = f"{module}.{alias.name}"
                    if (
                        module == "subprocess"
                        or module == "multiprocessing"
                        or (module == "os" and (alias.name in {"system", "popen"} or alias.name.startswith(("exec", "spawn", "posix_spawn"))))
                        or (module == "asyncio" and alias.name in {"create_subprocess_exec", "create_subprocess_shell"})
                        or (module == "concurrent.futures" and alias.name == "ProcessPoolExecutor")
                    ):
                        process_call_aliases.add(local_name)
                    module_aliases.setdefault(local_name, qualified)

        def call_path(node: ast.AST) -> tuple[str, ...]:
            parts: list[str] = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return tuple(reversed(parts))

        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in FORBIDDEN_IMPORTS:
                    external_imports.append(f"{relative}:{name}")
            if isinstance(node, ast.Call):
                called = None
                if isinstance(node.func, ast.Name):
                    called = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    called = node.func.attr
                path_parts = call_path(node.func)
                root_name = path_parts[0] if path_parts else ""
                root_module = module_aliases.get(root_name, root_name)
                process_call = (
                    (isinstance(node.func, ast.Name) and called in process_call_aliases)
                    or root_module.split(".")[0] in {"subprocess", "multiprocessing"}
                    or (
                        root_module == "os"
                        and called is not None
                        and (
                            called in {"system", "popen"}
                            or called.startswith(("exec", "spawn", "posix_spawn"))
                        )
                    )
                    or (
                        root_module == "asyncio"
                        and called in {"create_subprocess_exec", "create_subprocess_shell"}
                    )
                    or (
                        root_module.startswith("concurrent")
                        and called == "ProcessPoolExecutor"
                    )
                )
                if process_call:
                    process_executions.append(
                        f"{relative}:{getattr(node, 'lineno', 0)}:{'.'.join(path_parts) or called}"
                    )
                if called == "getenv":
                    environment_reads.append(f"{relative}:{getattr(node, 'lineno', 0)}:getenv")
                if path.name == "reconciliation.py" and called in {"prepare", "dispatch", "deliver"}:
                    reconciler_capabilities.append(f"{relative}:{getattr(node, 'lineno', 0)}:{called}")
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                environment_reads.append(f"{relative}:{getattr(node, 'lineno', 0)}:environ")
        functions = _function_material(path)

        def reachable_from(start: str) -> list[tuple[str, tuple[str, ...]]]:
            reachable = []
            pending = [start]
            seen_functions = set()
            while pending:
                current = pending.pop()
                if current in seen_functions or current not in functions:
                    continue
                seen_functions.add(current)
                materials, callees = functions[current]
                reachable.append((current, materials))
                pending.extend(callee for callee in callees if callee in functions)
            return reachable

        for name in functions:
            reachable = reachable_from(name)
            sql_statements = [
                re.sub(r"[^a-z0-9_]+", " ", statement)
                for _, materials in reachable
                for statement in materials
            ]
            writes_payment_ledger = any(
                re.search(
                    r"\b(update|insert into|delete from)\b[^;]*\b(payment_ledger|payment_commands)\b",
                    statement,
                )
                for statement in sql_statements
            )
            writes_payment = any(
                re.search(r"\b(update|insert into|delete from)\b[^;]*\bpayment_", statement)
                for statement in sql_statements
            )
            writes_handoff = any(
                re.search(r"\b(update|insert into|delete from)\b[^;]*\bhandoff_", statement)
                for statement in sql_statements
            )
            writes_reservation_execution = any(
                re.search(
                    r"\b(update|insert into|delete from)\b[^;]*\b"
                    r"(reservation_commands|reservation_outcomes|reservation_workflows|reservation_events)\b",
                    statement,
                )
                for statement in sql_statements
            )
            if "outbox" in name and writes_payment_ledger:
                outbox_ledger_references.append(
                    f"{relative}:{name}->" + ",".join(callee for callee, _ in reachable[1:])
                )
            if "handoff" in name and writes_payment:
                cross_workflow_writes.append(f"{relative}:{name}:handoff->payment")
            if "payment" in name and (writes_handoff or writes_reservation_execution):
                target = "handoff" if writes_handoff else "reservation-execution"
                cross_workflow_writes.append(f"{relative}:{name}:payment->{target}")
    if external_imports:
        failures.append(f"external capability imports: {external_imports}")
    if environment_reads:
        failures.append(f"environment/auth reads: {environment_reads}")
    if process_executions:
        failures.append(f"process execution capabilities: {process_executions}")
    if reconciler_capabilities:
        failures.append(f"reconciler capability calls: {reconciler_capabilities}")
    if outbox_ledger_references:
        failures.append(f"outbox writes financial ledger: {outbox_ledger_references}")
    if cross_workflow_writes:
        failures.append(f"cross-workflow writes: {cross_workflow_writes}")
    return {
        "python_files": len(paths),
        "external_imports": external_imports,
        "environment_reads": environment_reads,
        "process_executions": process_executions,
        "reconciler_capabilities": reconciler_capabilities,
        "outbox_ledger_references": outbox_ledger_references,
        "cross_workflow_writes": cross_workflow_writes,
    }


def _runtime_artifact(path: Path) -> bool:
    return path.suffix.lower() in _RUNTIME_SUFFIXES or path.name.endswith(("-wal", "-shm")) or "__pycache__" in path.parts


def check_required_files(failures: list[str], *, root: Path = ROOT) -> dict[str, Any]:
    missing = [relative for relative in REQUIRED if not (root / relative).is_file()]
    if missing:
        failures.append(f"missing required Phase 6 files: {missing}")
    evidence = root / EVIDENCE_RELATIVE
    files = tuple(path for path in evidence.rglob("*") if path.is_file()) if evidence.is_dir() else ()
    runtime = sorted(
        str(path.relative_to(root))
        for path in files
        if _runtime_artifact(path)
    )
    if runtime:
        failures.append(f"runtime artifact in evidence: {runtime}")
    fixed_names = {
        "README.md", "SHA256SUMS", "adversarial-review.md", "ci-result.json",
        "concurrency-result.json", "entry-baseline.json", "fault-matrix.json",
        "mutation-result.json", "package-manifest.json", "performance-result.json",
        "property-result.json", "restart-result.json", "schema-manifest.json",
        "validation-result.json",
    }
    unexpected = []
    for path in files:
        relative = path.relative_to(evidence)
        name = relative.name
        allowed = (
            len(relative.parts) == 1
            and (
                name in fixed_names
                or re.fullmatch(r"red-result-[a-z0-9-]+\.json", name) is not None
                or re.fullmatch(r"task(?:[4-9]|1[0-4])[-_][a-z0-9-]+\.patch", name) is not None
            )
        )
        if not allowed:
            unexpected.append(str(relative))
    if unexpected:
        failures.append(f"unexpected evidence files: {sorted(unexpected)}")
    return {
        "required": len(REQUIRED),
        "missing": missing,
        "runtime_artifacts": runtime,
        "unexpected": sorted(unexpected),
    }


def check_git_index(failures: list[str], *, root: Path = ROOT) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        failures.append("cannot inspect git index for Phase 6 closeout")
        return {"indexed": 0, "missing": []}
    indexed = {item for item in completed.stdout.split("\0") if item}
    try:
        expected = {str(path.relative_to(root)) for path in checksum_paths(root=root)}
    except (FileNotFoundError, ValueError) as exc:
        failures.append(f"cannot derive Phase 6 checksum inventory: {exc}")
        return {"indexed": len(indexed), "missing": []}
    missing = sorted(expected - indexed)
    if missing:
        failures.append(f"Phase 6 files are not tracked/staged: {missing}")
    return {"indexed": len(indexed), "missing": missing}


def _walk_claims(value: object, prefix: str = "") -> list[str]:
    positives = []
    if type(value) is dict:
        for key, child in value.items():
            location = f"{prefix}.{key}" if prefix else key
            lowered = key.lower()
            if any(marker in lowered for marker in ("network_calls", "live_provider_calls", "live_delivery_calls", "live_database_calls")):
                if not _exact_int(child, 0):
                    positives.append(f"{location}={child!r}")
            if any(marker in lowered for marker in ("postgresql_executed", "docker_executed", "live_capabilities_executed", "network_used", "provider_used")):
                exact_negative = (
                    child is False
                    or (type(child) is list and child == [])
                )
                if not exact_negative:
                    positives.append(f"{location}={child!r}")
            positives.extend(_walk_claims(child, location))
    elif type(value) is list:
        for index, child in enumerate(value):
            positives.extend(_walk_claims(child, f"{prefix}[{index}]"))
    return positives


def check_live_execution_claims(
    failures: list[str], *, evidence: Path = EVIDENCE
) -> dict[str, Any]:
    positives = []
    files = 0
    if not evidence.is_dir():
        failures.append("Phase 6 evidence directory missing")
        return {"files": 0, "positive_claims": []}
    for path in sorted(evidence.rglob("*")):
        if not path.is_file():
            continue
        files += 1
        if path.suffix.lower() == ".json":
            try:
                positives.extend(f"{path.name}:{item}" for item in _walk_claims(_load_json(path)))
            except (ValueError, json.JSONDecodeError) as exc:
                failures.append(f"invalid evidence JSON {path.name}: {exc}")
        elif path.suffix.lower() == ".md":
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(
                r"(?im)^.*\b(docker|postgresql|network|provider|pix|stripe|wise|supabase).*\b(executed|live|run|used)\s*:\s*(yes|true|1)\b.*$",
                text,
            ):
                positives.append(f"{path.name}:{match.group(0).strip()}")
    if positives:
        failures.append(f"positive live execution claims: {positives}")
    return {"files": files, "positive_claims": positives}


def check_workflow(failures: list[str], *, root: Path = ROOT) -> dict[str, Any]:
    path = root / ".github/workflows/phase6.yml"
    if not path.is_file():
        failures.append("Phase 6 workflow missing")
        return {"jobs": []}
    raw = path.read_text(encoding="utf-8")
    active = "\n".join(
        line.split("#", 1)[0].rstrip()
        for line in raw.splitlines()
        if line.split("#", 1)[0].strip()
    )
    jobs = (
        "static-validation", "full-suite", "properties",
        "fault-restart-contention", "mutations", "phase6-gate",
    )
    markers = (
        "name: phase-6-handoff-and-payments",
        "permissions:", "contents: read", "--cases 20000",
        "--restart-schedules 2000", "--contention-rounds 50",
        "generate_phase6_manifest.py --check", "validate_phase6.py",
        "python3 -m unittest discover -s tests -v",
        "PYTHONHASHSEED=1", "PYTHONHASHSEED=777",
    )
    for marker in markers:
        if marker not in active:
            failures.append(f"workflow missing active marker: {marker}")
    for job in jobs:
        if re.search(rf"(?m)^  {re.escape(job)}:\s*$", active) is None:
            failures.append(f"workflow missing job: {job}")
    if active.count("timeout-minutes: 15") != 6:
        failures.append("workflow must give all six jobs the exact 15-minute budget")
    needs = "needs: [static-validation, full-suite, properties, fault-restart-contention, mutations]"
    if needs not in active:
        failures.append("phase6-gate needs set mismatch")
    if re.search(r"(?im)^\s*if:\s*always\(\)", active):
        failures.append("phase6-gate must not use if: always()")
    job_matches = list(
        re.finditer(
            r"^  (static-validation|full-suite|properties|fault-restart-contention|mutations|phase6-gate):\s*$",
            active,
            re.MULTILINE,
        )
    )
    blocks = {}
    for index, match in enumerate(job_matches):
        end = job_matches[index + 1].start() if index + 1 < len(job_matches) else len(active)
        blocks[match.group(1)] = active[match.end():end]
    work_jobs = (
        "static-validation", "full-suite", "properties",
        "fault-restart-contention", "mutations",
    )
    checkout_jobs = [
        job for job in work_jobs if "actions/checkout@v4" in blocks.get(job, "")
    ]
    missing_checkout = sorted(set(work_jobs) - set(checkout_jobs))
    if missing_checkout:
        failures.append(f"Phase 6 workflow missing checkout: {missing_checkout}")
    job_markers = {
        "static-validation": ("validate_phase6.py", "generate_phase6_manifest.py --check"),
        "full-suite": ("unittest discover -s tests -v",),
        "properties": ("run_phase6_properties.py", "--cases 20000"),
        "fault-restart-contention": ("run_phase6_faults.py", "--restart-schedules 2000", "--contention-rounds 50"),
        "mutations": ("tests.test_phase6_mutation_runner", "run_phase6_mutations.py --write"),
    }
    for job, markers_for_job in job_markers.items():
        block = blocks.get(job, "")
        for marker in markers_for_job:
            if marker not in block:
                failures.append(f"Phase 6 workflow marker in wrong/missing job {job}: {marker}")
    if active.count("scripts/run_phase6_mutations.py --write") != 1:
        failures.append("Phase 6 workflow must run the integral mutation catalog exactly once")
    if active.count("tests.test_phase6_mutation_runner") != 2:
        failures.append("Phase 6 workflow must run focused mutation tests under two hash seeds")
    return {
        "jobs": list(jobs),
        "timeouts": active.count("timeout-minutes: 15"),
        "checkout_jobs": checkout_jobs,
    }


def check_closeout_payload(failures: list[str], payload: dict[str, Any]) -> None:
    top_keys = {
        "schema_version", "phase", "task", "status", "base_commit", "tdd",
        "gates", "scans", "network_calls", "live_provider_calls",
        "live_delivery_calls", "live_database_calls", "postgresql_executed",
        "live_capabilities_executed", "phase7_started", "rollout",
    }
    valid_top = (
        type(payload) is dict
        and set(payload) == top_keys
        and _exact_int(payload.get("schema_version"), 1)
        and payload.get("phase") == PHASE
        and _exact_int(payload.get("task"), 14)
        and payload.get("status") == "local_terminal_gates_passed"
        and type(payload.get("base_commit")) is str
        and re.fullmatch(r"[a-f0-9]{40}", payload["base_commit"]) is not None
        and _exact_int(payload.get("network_calls"), 0)
        and _exact_int(payload.get("live_provider_calls"), 0)
        and _exact_int(payload.get("live_delivery_calls"), 0)
        and _exact_int(payload.get("live_database_calls"), 0)
        and payload.get("postgresql_executed") is False
        and payload.get("live_capabilities_executed") is False
        and payload.get("phase7_started") is False
        and payload.get("rollout") == "NO-GO"
    )
    if not valid_top:
        failures.append("Task 14 closeout envelope mismatch")
        return
    tdd = payload.get("tdd")
    if type(tdd) is not dict or set(tdd) != {"initial_red", "hardening_reds", "focused_green"}:
        failures.append("Task 14 TDD envelope mismatch")
        return
    initial = tdd["initial_red"]
    if (
        type(initial) is not dict
        or set(initial) != {"command", "exit_code", "output_path", "output_sha256", "raw_output_versioned", "patch_path", "patch_sha256"}
        or initial.get("command") != "python3 -m unittest tests.test_phase6_closeout -v"
        or not _exact_int(initial.get("exit_code"), 1)
        or type(initial.get("output_path")) is not str
        or type(initial.get("output_sha256")) is not str
        or HASH_RE.fullmatch(initial["output_sha256"]) is None
        or initial.get("raw_output_versioned") is not False
        or initial.get("patch_path") != "docs/refactor/evidence/phase-06/task14-red.patch"
        or type(initial.get("patch_sha256")) is not str
        or HASH_RE.fullmatch(initial["patch_sha256"]) is None
    ):
        failures.append("Task 14 initial RED mismatch")
    hardening = tdd.get("hardening_reds")
    if type(hardening) is not list or len(hardening) < 6:
        failures.append("Task 14 hardening RED cardinality mismatch")
    else:
        for index, row in enumerate(hardening):
            if (
                type(row) is not dict
                or set(row) != {"label", "output_path", "output_sha256"}
                or type(row.get("label")) is not str
                or not row["label"]
                or type(row.get("output_path")) is not str
                or type(row.get("output_sha256")) is not str
                or HASH_RE.fullmatch(row["output_sha256"]) is None
            ):
                failures.append(f"Task 14 hardening RED mismatch at {index}")
                break
    focused = tdd.get("focused_green")
    if (
        type(focused) is not dict
        or set(focused) != {"command", "exit_code", "tests_run", "elapsed_seconds", "output_sha256"}
        or focused.get("command") != "python3 -m unittest tests.test_phase6_properties tests.test_phase6_closeout -v"
        or not _exact_int(focused.get("exit_code"), 0)
        or type(focused.get("tests_run")) is not int
        or focused["tests_run"] < 12
        or not _exact_float(focused.get("elapsed_seconds"))
        or type(focused.get("output_sha256")) is not str
        or HASH_RE.fullmatch(focused["output_sha256"]) is None
    ):
        failures.append("Task 14 focused GREEN mismatch")
    gates = payload.get("gates")
    gate_names = {"full_suite", "properties", "faults", "mutations", "manifests", "compileall", "git_checks"}
    if type(gates) is not dict or set(gates) != gate_names:
        failures.append("Task 14 gate inventory mismatch")
        return
    measured_keys = {"command", "exit_code", "elapsed_seconds", "max_rss_kb", "output_sha256", "output_bytes", "facts"}
    for name in ("full_suite", "properties", "faults", "mutations"):
        row = gates.get(name)
        invalid = (
            type(row) is not dict
            or set(row) != measured_keys
            or type(row.get("command")) is not str
            or not _exact_int(row.get("exit_code"), 0)
            or not _exact_float(row.get("elapsed_seconds"))
            or type(row.get("max_rss_kb")) is not int
            or row["max_rss_kb"] <= 0
            or type(row.get("output_sha256")) is not str
            or HASH_RE.fullmatch(row["output_sha256"]) is None
            or type(row.get("output_bytes")) is not int
            or row["output_bytes"] < 0
            or type(row.get("facts")) is not dict
        )
        if invalid:
            failures.append(f"Task 14 measured gate mismatch: {name}")
        elif row["elapsed_seconds"] > 900.0:
            failures.append(f"Task 14 CI budget exceeded: {name}")
    facts = {name: gates[name].get("facts", {}) for name in ("full_suite", "properties", "faults", "mutations") if type(gates.get(name)) is dict}
    if type(facts.get("full_suite", {}).get("tests_run")) is not int or facts["full_suite"]["tests_run"] < 616:
        failures.append("Task 14 full suite cardinality mismatch")
    if not _exact_json(facts.get("properties"), {"cases": 20000, "seed": SEED}):
        failures.append("Task 14 property facts mismatch")
    if not _exact_json(facts.get("faults"), {"fault_points": 27, "restart_schedules": 2000, "contention_rows": 200}):
        failures.append("Task 14 fault facts mismatch")
    if not _exact_json(facts.get("mutations"), {"mutants": 12, "all_killed": True}):
        failures.append("Task 14 mutation facts mismatch")
    for name in ("manifests", "compileall", "git_checks"):
        if not _exact_json(gates.get(name), {"result": "passed"}):
            failures.append(f"Task 14 simple gate mismatch: {name}")
    if not _exact_json(
        payload.get("scans"),
        {
            "package_purity": "passed",
            "live_claims": "passed",
            "secrets_pii": "passed",
            "postgresql": "static_ddl_only",
        },
    ):
        failures.append("Task 14 scan summary mismatch")


def check_ci_payload(failures: list[str], payload: dict[str, Any]) -> None:
    top_keys = {
        "schema_version", "phase", "implementation_commit", "checked_at_utc",
        "all_success", "workflow_count", "workflows",
        "phase7_authorized_after_closeout", "phase7_started", "rollout",
    }
    workflow_names = (
        "phase-0-validation",
        "phase-1-characterization",
        "phase-2-domain",
        "phase-3-lookups",
        "phase-4-confirmation",
        "phase-5-durable-execution",
        "phase-6-handoff-and-payments",
    )
    valid_top = (
        type(payload) is dict
        and set(payload) == top_keys
        and _exact_int(payload.get("schema_version"), 1)
        and payload.get("phase") == PHASE
        and type(payload.get("implementation_commit")) is str
        and re.fullmatch(r"[a-f0-9]{40}", payload["implementation_commit"]) is not None
        and type(payload.get("checked_at_utc")) is str
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload["checked_at_utc"]) is not None
        and payload.get("all_success") is True
        and _exact_int(payload.get("workflow_count"), 7)
        and payload.get("phase7_authorized_after_closeout") is False
        and payload.get("phase7_started") is False
        and payload.get("rollout") == "NO-GO"
    )
    if not valid_top:
        failures.append("CI result envelope mismatch")
    workflows = payload.get("workflows")
    if type(workflows) is not list or len(workflows) != 7:
        failures.append("CI workflow cardinality mismatch")
        return
    for index, (row, expected_name) in enumerate(zip(workflows, workflow_names)):
        expected_keys = {"id", "name", "conclusion", "url"}
        if index == 6:
            expected_keys.add("jobs")
        valid = (
            type(row) is dict
            and set(row) == expected_keys
            and type(row.get("id")) is int
            and row["id"] > 0
            and row.get("name") == expected_name
            and row.get("conclusion") == "success"
            and type(row.get("url")) is str
            and re.fullmatch(r"https://github\.com/[^/]+/[^/]+/actions/runs/\d+", row["url"]) is not None
        )
        if not valid:
            failures.append(f"CI workflow mismatch at {index}")
            return
    expected_jobs = (
        "static-validation", "full-suite", "properties",
        "fault-restart-contention", "mutations", "phase6-gate",
    )
    jobs = workflows[-1].get("jobs")
    if type(jobs) is not list or len(jobs) != 6:
        failures.append("Phase 6 CI job cardinality mismatch")
        return
    for index, (row, expected_name) in enumerate(zip(jobs, expected_jobs)):
        if (
            type(row) is not dict
            or set(row) != {"id", "name", "conclusion"}
            or type(row.get("id")) is not int
            or row["id"] <= 0
            or row.get("name") != expected_name
            or row.get("conclusion") != "success"
        ):
            failures.append(f"Phase 6 CI job mismatch at {index}")
            return


def check_metrics(
    failures: list[str],
    *,
    validation: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
    evidence: Path = EVIDENCE,
) -> None:
    if validation is None:
        try:
            validation = _load_json(evidence / "validation-result.json")
            performance = _load_json(evidence / "performance-result.json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"metrics unavailable: {exc}")
            return
    assert performance is not None
    validation_keys = {"schema_version", "phase", "result", "command", "exit_code", "tests_run", "unittest_elapsed_seconds", "elapsed_seconds", "max_rss_kb", "output_sha256", "raw_output_versioned", "network_calls", "live_provider_calls", "live_delivery_calls", "live_database_calls", "rollout"}
    performance_keys = {"schema_version", "phase", "result", "measurement", "command", "exit_code", "tests_run", "elapsed_seconds", "max_rss_kb", "ci_timeout_seconds", "output_sha256", "raw_output_versioned", "nondeterministic_metrics_local_only", "postgresql_executed", "live_capabilities_executed", "rollout"}
    valid_validation = (
        type(validation) is dict
        and set(validation) == validation_keys
        and _exact_int(validation.get("schema_version"), 1)
        and validation.get("phase") == PHASE
        and validation.get("result") == "passed"
        and validation.get("command") == "python3 -m unittest discover -s tests -v"
        and _exact_int(validation.get("exit_code"), 0)
        and type(validation.get("tests_run")) is int and validation["tests_run"] >= 616
        and _exact_float(validation.get("unittest_elapsed_seconds"))
        and _exact_float(validation.get("elapsed_seconds"))
        and type(validation.get("max_rss_kb")) is int and validation["max_rss_kb"] > 0
        and type(validation.get("output_sha256")) is str and HASH_RE.fullmatch(validation["output_sha256"]) is not None
        and validation.get("raw_output_versioned") is False
        and all(_exact_int(validation.get(key), 0) for key in ("network_calls", "live_provider_calls", "live_delivery_calls", "live_database_calls"))
        and validation.get("rollout") == "NO-GO"
    )
    valid_performance = (
        type(performance) is dict
        and set(performance) == performance_keys
        and _exact_int(performance.get("schema_version"), 1)
        and performance.get("phase") == PHASE
        and performance.get("result") == "passed"
        and performance.get("measurement") == "fresh full unittest suite"
        and performance.get("command") == "python3 -m unittest discover -s tests -v"
        and _exact_int(performance.get("exit_code"), 0)
        and type(performance.get("tests_run")) is int and performance["tests_run"] >= 616
        and _exact_float(performance.get("elapsed_seconds"))
        and type(performance.get("max_rss_kb")) is int and performance["max_rss_kb"] > 0
        and _exact_int(performance.get("ci_timeout_seconds"), 900)
        and type(performance.get("output_sha256")) is str and HASH_RE.fullmatch(performance["output_sha256"]) is not None
        and performance.get("raw_output_versioned") is False
        and performance.get("nondeterministic_metrics_local_only") is True
        and performance.get("postgresql_executed") is False
        and performance.get("live_capabilities_executed") is False
        and performance.get("rollout") == "NO-GO"
    )
    if not valid_validation:
        failures.append("validation-result closed schema mismatch")
    if not valid_performance:
        failures.append("performance-result closed schema mismatch")
    if valid_validation and valid_performance and (
        validation["tests_run"] != performance["tests_run"]
        or validation["elapsed_seconds"] != performance["elapsed_seconds"]
        or validation["max_rss_kb"] != performance["max_rss_kb"]
        or validation["output_sha256"] != performance["output_sha256"]
    ):
        failures.append("validation/performance metrics diverge")


def check_schema_and_manifests(failures: list[str]) -> dict[str, Any]:
    tables = schema_contract()
    expected_tables = (
        "handoff_workflows", "handoff_events", "handoff_outbox", "handoff_receipts",
        "payment_workflows", "payment_events", "payment_evidence_claims",
        "payment_commands", "payment_ledger", "payment_outbox", "payment_receipts",
    )
    if SCHEMA_VERSION != 1 or tuple(table.name for table in tables) != expected_tables:
        failures.append("Phase 6 schema contract mismatch")
    if (ROOT / "schemas/phase6/sqlite.sql").read_text(encoding="utf-8") != render_sqlite():
        failures.append("tracked SQLite DDL is stale")
    if (ROOT / "schemas/phase6/postgresql.sql").read_text(encoding="utf-8") != render_postgresql():
        failures.append("tracked PostgreSQL DDL is stale")
    package_manifest = build_package_manifest()
    expected_package_files = {
        "reservation_followup/__init__.py",
        "reservation_followup/handoff.py",
        "reservation_followup/payment.py",
        "reservation_followup/projection.py",
        "reservation_followup/properties.py",
        "reservation_followup/reconciliation.py",
        "reservation_followup/schema.py",
        "reservation_followup/serialization.py",
        "reservation_followup/sqlite_store.py",
        "reservation_followup/types.py",
        "reservation_followup/workers.py",
    }
    if (
        package_manifest.get("package") != "reservation_followup"
        or not _exact_int(package_manifest.get("python_file_count"), 11)
        or {item.get("path") for item in package_manifest.get("files", [])}
        != expected_package_files
    ):
        failures.append("Phase 6 package manifest contract mismatch")
    failures.extend(check_manifests())
    return {"tables": len(tables), "schema_version": SCHEMA_VERSION}


def check_docs(failures: list[str]) -> dict[str, Any]:
    paths = (
        ROOT / "README.md",
        ROOT / "docs/refactor/README.md",
        ROOT / "docs/refactor/evidence/README.md",
        ROOT / "docs/refactor/phases/phase-06-handoff-and-payments.md",
        EVIDENCE / "README.md",
        EVIDENCE / "adversarial-review.md",
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths if path.is_file())
    for marker in ("NO-GO", "phase7_started=false", "PostgreSQL"):
        if marker not in text:
            failures.append(f"Phase 6 documentation missing marker: {marker}")
    if "Fase 7" not in text and "Phase 7" not in text:
        failures.append("Phase 7 blocked status missing")
    return {"documents": len(paths)}


def main() -> int:
    failures: list[str] = []
    required = check_required_files(failures)
    git_index = check_git_index(failures)
    payloads = {}
    for name in (
        "property-result.json", "fault-matrix.json", "restart-result.json",
        "concurrency-result.json", "mutation-result.json",
    ):
        path = EVIDENCE / name
        try:
            payloads[name] = _load_json(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot load {name}: {exc}")
    if "property-result.json" in payloads:
        check_property_payload(failures, payloads["property-result.json"])
    if all(name in payloads for name in ("fault-matrix.json", "restart-result.json", "concurrency-result.json")):
        check_fault_payloads(failures, payloads["fault-matrix.json"], payloads["restart-result.json"], payloads["concurrency-result.json"])
    if "mutation-result.json" in payloads:
        check_mutation_payload(failures, payloads["mutation-result.json"])
    closeout_path = EVIDENCE / "red-result-closeout.json"
    try:
        closeout_payload = _load_json(closeout_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"cannot load red-result-closeout.json: {exc}")
    else:
        check_closeout_payload(failures, closeout_payload)
    metrics_failures: list[str] = []
    check_metrics(metrics_failures)
    failures.extend(metrics_failures)
    ci_checked = False
    ci_path = EVIDENCE / "ci-result.json"
    if ci_path.is_file():
        ci_checked = True
        try:
            ci_payload = _load_json(ci_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"cannot load ci-result.json: {exc}")
        else:
            check_ci_payload(failures, ci_payload)
    schema = check_schema_and_manifests(failures)
    purity = check_package_purity(failures)
    live = check_live_execution_claims(failures)
    workflow = check_workflow(failures)
    docs = check_docs(failures)
    links = check_markdown_links(failures)
    sensitive = check_secrets_and_pii(failures)
    result = {
        "schema_version": 1,
        "phase": PHASE,
        "result": "passed" if not failures else "failed",
        "failures": failures,
        "required": required,
        "git_index": git_index,
        "schema": schema,
        "purity": purity,
        "live_claims": live,
        "workflow": workflow,
        "docs": docs,
        "markdown_links_checked": links,
        "sensitive_files_checked": sensitive,
        "ci_checked": ci_checked,
        "catalogs": {"property_modes": 16, "fault_points": 27, "restart_points": 12, "contention_domains": 4, "mutants": 12},
        "rollout": "NO-GO",
        "phase7_started": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
