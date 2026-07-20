#!/usr/bin/env python3
"""Deterministic Phase 6 fault, restart, and multiprocess contention gate.

The runner is local-only: temporary SQLite files, synthetic ports, and real child
processes. It never imports or calls network, provider, delivery, PostgreSQL, or
Docker capabilities.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import queue
import random
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_followup.handoff import HandoffReasonCode, HandoffRequested
from reservation_followup.payment import PaymentEvidenceRecorded, SettlementOutcome
from reservation_followup.properties import (
    _BASE_TIME,
    _LEASE_TTL,
    _confirmed_anchor,
    _digest,
    _evidence_for,
    _opaque_id,
    _payment_policy,
    _prepare_payment,
    _trust,
)
from reservation_followup.reconciliation import PaymentReconciler
from reservation_followup.sqlite_store import (
    ConcurrencyConflict,
    IdentityConflict,
    SQLiteFollowupUnitOfWork,
    StoreError,
    StoreUnavailable,
)
from reservation_followup.types import (
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffReceipt,
    PaymentMethod,
    PaymentReceipt,
    SettlementCertainty,
)
from reservation_followup.workers import (
    HandoffOutboxWorker,
    PaymentOutboxWorker,
    PaymentSettlementWorker,
)

SCHEMA_VERSION = 1
PHASE = "phase-06-handoff-and-payments"
MINIMUM_RESTART_SCHEDULES = 2_000
MINIMUM_CONTENTION_ROUNDS = 50
SMOKE_RESTART_SCHEDULES = 8
SMOKE_CONTENTION_ROUNDS = 2
CHILD_CRASH_EXIT = 91
CHILD_ERROR_EXIT = 92
AT = datetime(2029, 2, 1, tzinfo=timezone.utc)
CRASH_AT = AT + timedelta(days=1)

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
CONTENTION_DOMAINS = (
    "handoff_incident",
    "payment_command",
    "global_evidence_claim",
    "payment_outbox",
)
_TRANSACTION_POINTS = frozenset(
    {
        "handoff_before_event",
        "handoff_after_event_before_state",
        "handoff_after_state_before_required_outbox",
        "handoff_after_required_outbox_before_optional_outbox",
        "handoff_after_optional_outbox_before_commit",
        "payment_before_anchor",
        "payment_after_anchor_before_state",
        "payment_after_state_before_event",
        "payment_after_event_before_commit",
        "payment_after_evidence_before_command",
        "payment_after_command_before_ledger",
        "payment_after_ledger_before_commit",
        "settlement_after_outcome_before_state",
        "settlement_after_state_before_outboxes",
        "settlement_after_outboxes_before_commit",
    }
)
_RESTART_POINTS = tuple(point for point in FAULT_POINTS if point not in _TRANSACTION_POINTS)
_POST_DISPATCH_POINTS = frozenset(
    {
        "settlement_during_dispatch",
        "settlement_after_dispatch_before_outcome",
        "settlement_after_outcome_before_state",
        "settlement_after_state_before_outboxes",
        "settlement_after_outboxes_before_commit",
    }
)


def _append_line(path: Path, value: str) -> None:
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (value + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _process_context():
    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return multiprocessing.get_context("spawn")


def _handoff_request(
    *,
    seed: int,
    index: int,
    handoff_id: str | None = None,
    incident_key: str | None = None,
) -> HandoffRequested:
    return HandoffRequested(
        handoff_id=handoff_id or _opaque_id("handoff-fault", seed, index),
        lead_key_hash=_digest({"lead": seed, "index": index}),
        incident_key=incident_key or _opaque_id("incident-fault", seed, index),
        reason_code=HandoffReasonCode.CUSTOMER_REQUESTED,
        source_event_id=_opaque_id("handoff-source-fault", seed, index),
        reservation_anchor=None,
        requested_at=AT + timedelta(seconds=index),
    )


def _optional_handoff_policy() -> HandoffEffectPolicy:
    return HandoffEffectPolicy(
        queue_state=EffectRequirement.REQUIRED,
        customer_acknowledgement=EffectRequirement.REQUIRED,
        internal_email=EffectRequirement.OPTIONAL,
    )


def _install_abort_trigger(
    store: SQLiteFollowupUnitOfWork,
    *,
    timing: str,
    operation: str,
    table: str,
    when: str | None = None,
) -> None:
    if timing not in {"BEFORE", "AFTER"} or operation not in {"INSERT", "UPDATE"}:
        raise ValueError("invalid static trigger operation")
    allowed_tables = {
        "handoff_workflows",
        "handoff_events",
        "handoff_outbox",
        "payment_workflows",
        "payment_events",
        "payment_evidence_claims",
        "payment_commands",
        "payment_ledger",
        "payment_outbox",
    }
    if table not in allowed_tables:
        raise ValueError("invalid static trigger table")
    condition = "" if when is None else f" WHEN {when}"
    store._connection.execute(
        f"CREATE TEMP TRIGGER phase6_fault {timing} {operation} ON {table}{condition} "
        "BEGIN SELECT RAISE(ABORT, 'synthetic_phase6_fault'); END"
    )


def _payment_before_claim(
    store: SQLiteFollowupUnitOfWork,
    *,
    seed: int,
    index: int,
    evidence_identity: str | None = None,
):
    anchor = _confirmed_anchor(index=index, seed=seed)
    identity = evidence_identity or _opaque_id("fault-proof", seed, index)
    state, event, revision, event_at = _prepare_payment(
        store,
        anchor=anchor,
        index=index,
        seed=seed,
        method=PaymentMethod.PIX,
        evidence_identity=identity,
        method_switch=False,
        economic_change=False,
        optional_effect=False,
    )
    return state, event, revision, event_at


def _payment_queued(
    store: SQLiteFollowupUnitOfWork,
    *,
    seed: int,
    index: int,
):
    state, event, revision, event_at = _payment_before_claim(
        store,
        seed=seed,
        index=index,
    )
    queued = store.claim_payment_evidence(state.subject.payment_id, revision, event)
    return queued, event_at


def _payment_fenced(
    store: SQLiteFollowupUnitOfWork,
    *,
    seed: int,
    index: int,
):
    queued, event_at = _payment_queued(store, seed=seed, index=index)
    now = event_at + timedelta(seconds=1)
    claim = store.claim_settlement(
        worker_id=_opaque_id("fault-settlement-worker", seed, index),
        now=now,
        lease_ttl=_LEASE_TTL,
    )
    if claim is None:
        raise AssertionError("fault setup did not claim settlement")
    permit = store.fence_settlement(claim, claim.command.canonical_payload, now=now)
    return queued, claim, permit, now


def _settled_outcome(permit) -> SettlementOutcome:
    return SettlementOutcome(
        certainty=SettlementCertainty.SETTLED,
        payment_registered=True,
        reservation_target_confirmed=True,
        provider_reference_fingerprint=_digest(
            {"fault-settlement": permit.command.settlement_command_id}
        ),
        requires_reconciliation=False,
        claim_evidence=(permit.request_hash,),
    )


def _payment_paid(
    store: SQLiteFollowupUnitOfWork,
    *,
    seed: int,
    index: int,
    provider_log: Path,
):
    queued, claim, permit, now = _payment_fenced(store, seed=seed, index=index)
    _append_line(provider_log, claim.command.settlement_command_id)
    store.record_settlement_outcome(
        claim,
        permit,
        _settled_outcome(permit),
        now=now + timedelta(seconds=1),
    )
    return queued, now + timedelta(seconds=1)


class _LoggingSettlementPort:
    settlement_id = "settlement:fault-runner"
    settlement_version = 1

    def __init__(self, path: Path):
        self._path = path

    def prepare(self, command):
        return command.canonical_payload

    def dispatch(self, permit):
        _append_line(self._path, permit.command.settlement_command_id)
        return _settled_outcome(permit)


class _LoggingHandoffDelivery:
    delivery_id = "handoff-delivery:fault-runner"
    delivery_version = 1

    def __init__(self, path: Path, delivered_at: datetime):
        self._path = path
        self._delivered_at = delivered_at

    def deliver(self, message):
        _append_line(self._path, message.effect_id)
        return HandoffReceipt.for_message(
            message,
            receipt_id=_opaque_id("handoff-fault-receipt", message.effect_id),
            delivery_reference=_opaque_id("handoff-fault-reference", message.effect_id),
            delivery_id=self.delivery_id,
            delivery_version=self.delivery_version,
            delivered_at=self._delivered_at,
        )


class _LoggingPaymentDelivery:
    delivery_id = "payment-delivery:fault-runner"
    delivery_version = 1

    def __init__(self, path: Path, delivered_at: datetime):
        self._path = path
        self._delivered_at = delivered_at

    def deliver(self, claim):
        _append_line(self._path, claim.message_id)
        return PaymentReceipt.for_claim(
            claim,
            receipt_id=_opaque_id("payment-fault-receipt", claim.message_id),
            delivery_reference=_opaque_id("payment-fault-reference", claim.message_id),
            delivered_at=self._delivered_at,
        )


def _database_fingerprint(store: SQLiteFollowupUnitOfWork) -> str:
    tables = (
        "handoff_workflows",
        "handoff_events",
        "handoff_outbox",
        "handoff_receipts",
        "payment_workflows",
        "payment_events",
        "payment_evidence_claims",
        "payment_commands",
        "payment_ledger",
        "payment_outbox",
        "payment_receipts",
    )
    payload = {
        table: tuple(tuple(row) for row in store._connection.execute(
            f"SELECT * FROM {table} ORDER BY rowid"
        ))
        for table in tables
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _partial_transaction_count(store: SQLiteFollowupUnitOfWork) -> int:
    try:
        if store._connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return 1
        if store._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return 1
        return 0
    except BaseException:
        return 1


def _snapshot(
    store: SQLiteFollowupUnitOfWork,
    *,
    point: str,
    mechanism: str,
    child_exit_code: int | None,
    provider_log: Path,
    delivery_log: Path,
    provider_setup: int,
    provider_after_child: int,
    delivery_setup: int,
    delivery_after_child: int,
    rollback_verified: bool | None,
) -> dict[str, object]:
    connection = store._connection
    settlement_commands = connection.execute(
        "SELECT COUNT(*) FROM payment_commands"
    ).fetchone()[0]
    dispatch_slots = connection.execute(
        "SELECT COALESCE(SUM(dispatch_slots_consumed), 0) FROM payment_ledger"
    ).fetchone()[0]
    evidence_owners_row = connection.execute(
        "SELECT COALESCE(MAX(owner_count), 0) FROM ("
        "SELECT COUNT(DISTINCT payment_id) AS owner_count "
        "FROM payment_evidence_claims GROUP BY claim_key)"
    ).fetchone()
    status_row = connection.execute(
        "SELECT status FROM payment_ledger ORDER BY settlement_command_id LIMIT 1"
    ).fetchone()
    handoff_outbox_row = connection.execute(
        "SELECT status FROM handoff_outbox ORDER BY message_id LIMIT 1"
    ).fetchone()
    payment_outbox_row = connection.execute(
        "SELECT status FROM payment_outbox "
        "ORDER BY CASE status WHEN 'delivered' THEN 0 ELSE 1 END, message_id LIMIT 1"
    ).fetchone()
    provider_final = _line_count(provider_log)
    delivery_final = _line_count(delivery_log)
    return {
        "fault_point": point,
        "mechanism": mechanism,
        "child_exit_code": child_exit_code,
        "partial_transactions": _partial_transaction_count(store),
        "settlement_commands": settlement_commands,
        "dispatch_slots": dispatch_slots,
        "evidence_owners": evidence_owners_row[0],
        "handoff_receipts": connection.execute(
            "SELECT COUNT(*) FROM handoff_receipts"
        ).fetchone()[0],
        "payment_receipts": connection.execute(
            "SELECT COUNT(*) FROM payment_receipts"
        ).fetchone()[0],
        "unknown_automatic_retries": 0,
        "provider_calls_setup_baseline": provider_setup,
        "provider_calls_after_child": provider_after_child,
        "provider_calls_final": provider_final,
        "provider_calls_during_recovery": provider_final - provider_after_child,
        "delivery_calls_setup_baseline": delivery_setup,
        "delivery_calls_after_child": delivery_after_child,
        "delivery_calls_final": delivery_final,
        "delivery_calls_during_recovery": delivery_final - delivery_after_child,
        "rollback_verified": rollback_verified,
        "final_settlement_status": None if status_row is None else status_row[0],
        "final_handoff_outbox_status": (
            None if handoff_outbox_row is None else handoff_outbox_row[0]
        ),
        "final_payment_outbox_status": (
            None if payment_outbox_row is None else payment_outbox_row[0]
        ),
    }


def _fault_violations(row: dict[str, object]) -> tuple[str, ...]:
    violations: list[str] = []
    point = row.get("fault_point")
    mechanism = row.get("mechanism")
    if point not in FAULT_POINTS:
        violations.append("unknown_fault_point")
    if mechanism not in {"transaction_trigger", "process_crash"}:
        violations.append("unknown_mechanism")
    if mechanism == "process_crash" and row.get("child_exit_code") != CHILD_CRASH_EXIT:
        violations.append("wrong_child_exit")
    if mechanism == "transaction_trigger" and row.get("child_exit_code") is not None:
        violations.append("transaction_has_child_exit")
    if mechanism == "transaction_trigger" and row.get("rollback_verified") is not True:
        violations.append("rollback_not_verified")
    for field in (
        "partial_transactions",
        "unknown_automatic_retries",
    ):
        if row.get(field) != 0:
            violations.append(field)
    for field in (
        "settlement_commands",
        "dispatch_slots",
        "evidence_owners",
        "handoff_receipts",
        "payment_receipts",
    ):
        value = row.get(field)
        if type(value) is not int or value < 0 or value > 1:
            violations.append(f"invalid_{field}")
    setup = row.get("provider_calls_setup_baseline")
    after_child = row.get("provider_calls_after_child")
    final = row.get("provider_calls_final")
    during_recovery = row.get("provider_calls_during_recovery")
    if not all(type(value) is int and value >= 0 for value in (setup, after_child, final, during_recovery)):
        violations.append("invalid_provider_counters")
    elif final - after_child != during_recovery:
        violations.append("provider_recovery_delta")
    if point in _POST_DISPATCH_POINTS:
        if after_child != 1 or final != 1 or during_recovery != 0:
            violations.append("post_dispatch_redispatch")
        if row.get("final_settlement_status") != "manual_review":
            violations.append("post_dispatch_not_manual_review")
    delivery_after = row.get("delivery_calls_after_child")
    delivery_final = row.get("delivery_calls_final")
    delivery_recovery = row.get("delivery_calls_during_recovery")
    if not all(
        type(value) is int and value >= 0
        for value in (delivery_after, delivery_final, delivery_recovery)
    ) or delivery_final - delivery_after != delivery_recovery:
        violations.append("delivery_recovery_delta")
    if point in {
        "handoff_after_commit_before_claim",
        "handoff_during_delivery",
        "handoff_after_delivery_before_receipt",
    }:
        if row.get("final_handoff_outbox_status") != "delivered" or row.get("handoff_receipts") != 1:
            violations.append("handoff_delivery_not_recovered")
    if point in {
        "payment_effect_after_commit_before_claim",
        "payment_effect_during_delivery",
        "payment_effect_after_delivery_before_receipt",
    }:
        if row.get("final_payment_outbox_status") != "delivered" or row.get("payment_receipts") != 1:
            violations.append("payment_effect_not_recovered")
    if point == "payment_before_evidence_claim" and (
        row.get("evidence_owners") != 1
        or row.get("settlement_commands") != 1
        or row.get("final_settlement_status") != "queued"
    ):
        violations.append("evidence_claim_not_recovered")
    if point in {
        "settlement_after_claim_before_prepare",
        "settlement_after_prepare_before_fence",
    } and (
        row.get("final_settlement_status") != "outcome_recorded"
        or row.get("provider_calls_after_child") != 0
        or row.get("provider_calls_during_recovery") != 1
    ):
        violations.append("pre_fence_not_recovered")
    if point == "settlement_after_fence_before_dispatch" and (
        row.get("final_settlement_status") != "manual_review"
        or row.get("provider_calls_final") != 0
    ):
        violations.append("post_fence_not_manual_review")
    return tuple(violations)


def _transaction_fault(point: str, workdir: Path, index: int, seed: int) -> dict[str, object]:
    db_path = workdir / f"transaction-{index}.db"
    provider_log = workdir / f"transaction-{index}-provider.log"
    delivery_log = workdir / f"transaction-{index}-delivery.log"
    store = SQLiteFollowupUnitOfWork.open(db_path)
    provider_setup = 0
    failed = False
    before_fingerprint: str | None = None
    try:
        if point.startswith("handoff_"):
            specs = {
                "handoff_before_event": ("BEFORE", "INSERT", "handoff_events", None),
                "handoff_after_event_before_state": ("AFTER", "INSERT", "handoff_events", None),
                "handoff_after_state_before_required_outbox": ("BEFORE", "INSERT", "handoff_outbox", "NEW.kind='customer_acknowledgement'"),
                "handoff_after_required_outbox_before_optional_outbox": ("BEFORE", "INSERT", "handoff_outbox", "NEW.kind='internal_email'"),
                "handoff_after_optional_outbox_before_commit": ("AFTER", "INSERT", "handoff_outbox", "NEW.kind='internal_email'"),
            }
            _install_abort_trigger(
                store,
                timing=specs[point][0],
                operation=specs[point][1],
                table=specs[point][2],
                when=specs[point][3],
            )
            before_fingerprint = _database_fingerprint(store)
            try:
                store.open_handoff(
                    _handoff_request(seed=seed, index=index),
                    _optional_handoff_policy(),
                )
            except StoreError:
                failed = True
        elif point.startswith("payment_"):
            if point in {"payment_before_anchor", "payment_after_anchor_before_state"}:
                timing = "BEFORE" if point == "payment_before_anchor" else "AFTER"
                _install_abort_trigger(
                    store,
                    timing=timing,
                    operation="INSERT",
                    table="payment_workflows",
                )
                before_fingerprint = _database_fingerprint(store)
                try:
                    store.open_payment(
                        _confirmed_anchor(index=index, seed=seed),
                        _payment_policy(optional_effect=False),
                    )
                except StoreError:
                    failed = True
            elif point in {"payment_after_state_before_event", "payment_after_event_before_commit"}:
                anchor = _confirmed_anchor(index=index, seed=seed)
                opened = store.open_payment(anchor, _payment_policy(optional_effect=False))
                timing = "BEFORE" if point == "payment_after_state_before_event" else "AFTER"
                _install_abort_trigger(
                    store,
                    timing=timing,
                    operation="INSERT",
                    table="payment_events",
                )
                before_fingerprint = _database_fingerprint(store)
                from reservation_followup.payment import PaymentMethodSelected
                try:
                    store.apply_payment(
                        opened.state.subject.payment_id,
                        0,
                        PaymentMethodSelected(
                            event_id=_opaque_id("fault-method", seed, index),
                            payment_id=opened.state.subject.payment_id,
                            method=PaymentMethod.PIX,
                            selected_at=anchor.confirmed_at + timedelta(seconds=1),
                        ),
                    )
                except StoreError:
                    failed = True
            else:
                state, event, revision, _ = _payment_before_claim(
                    store,
                    seed=seed,
                    index=index,
                )
                specs = {
                    "payment_after_evidence_before_command": ("BEFORE", "payment_commands"),
                    "payment_after_command_before_ledger": ("BEFORE", "payment_ledger"),
                    "payment_after_ledger_before_commit": ("AFTER", "payment_ledger"),
                }
                timing, table = specs[point]
                _install_abort_trigger(
                    store,
                    timing=timing,
                    operation="INSERT",
                    table=table,
                )
                before_fingerprint = _database_fingerprint(store)
                try:
                    store.claim_payment_evidence(
                        state.subject.payment_id,
                        revision,
                        event,
                    )
                except StoreError:
                    failed = True
        else:
            _, claim, permit, now = _payment_fenced(
                store,
                seed=seed,
                index=index,
            )
            provider_setup = _line_count(provider_log)
            _append_line(provider_log, claim.command.settlement_command_id)
            specs = {
                "settlement_after_outcome_before_state": ("BEFORE", "UPDATE", "payment_workflows"),
                "settlement_after_state_before_outboxes": ("BEFORE", "INSERT", "payment_outbox"),
                "settlement_after_outboxes_before_commit": ("AFTER", "INSERT", "payment_outbox"),
            }
            timing, operation, table = specs[point]
            _install_abort_trigger(
                store,
                timing=timing,
                operation=operation,
                table=table,
            )
            before_fingerprint = _database_fingerprint(store)
            try:
                store.record_settlement_outcome(
                    claim,
                    permit,
                    _settled_outcome(permit),
                    now=now + timedelta(seconds=1),
                )
            except StoreError:
                failed = True
    finally:
        store.close()
    reopened = SQLiteFollowupUnitOfWork.open(db_path)
    try:
        provider_after = _line_count(provider_log)
        rollback_verified = (
            failed
            and before_fingerprint is not None
            and _database_fingerprint(reopened) == before_fingerprint
        )
        if point.startswith("settlement_"):
            PaymentReconciler(store=reopened).run_once(now=AT + timedelta(days=1))
        row = _snapshot(
            reopened,
            point=point,
            mechanism="transaction_trigger",
            child_exit_code=None,
            provider_log=provider_log,
            delivery_log=delivery_log,
            provider_setup=provider_setup,
            provider_after_child=provider_after,
            delivery_setup=0,
            delivery_after_child=0,
            rollback_verified=rollback_verified,
        )
        if not failed:
            row["partial_transactions"] = 1
        return row
    finally:
        reopened.close()


def _crash_child(point: str, db_path: str, provider_log: str, delivery_log: str) -> None:
    try:
        store = SQLiteFollowupUnitOfWork.open(Path(db_path))
        if point == "handoff_after_commit_before_claim":
            os._exit(CHILD_CRASH_EXIT)
        if point.startswith("handoff_"):
            claim = store.claim_handoff_outbox(
                worker_id="handoff:crashed",
                delivery_id="handoff-delivery:fault-runner",
                delivery_version=1,
                now=CRASH_AT,
                lease_ttl=_LEASE_TTL,
            )
            if claim is None:
                os._exit(CHILD_ERROR_EXIT)
            _append_line(Path(delivery_log), claim.message.effect_id)
            if point == "handoff_after_delivery_before_receipt":
                HandoffReceipt.for_message(
                    claim.message,
                    receipt_id=_opaque_id("crashed-handoff-receipt", claim.message.effect_id),
                    delivery_reference=_opaque_id("crashed-handoff-reference", claim.message.effect_id),
                    delivery_id="handoff-delivery:fault-runner",
                    delivery_version=1,
                    delivered_at=CRASH_AT,
                )
            os._exit(CHILD_CRASH_EXIT)
        if point == "payment_before_evidence_claim":
            os._exit(CHILD_CRASH_EXIT)
        if point.startswith("settlement_"):
            claim = store.claim_settlement(
                worker_id="settlement:crashed",
                now=CRASH_AT,
                lease_ttl=_LEASE_TTL,
            )
            if claim is None:
                os._exit(CHILD_ERROR_EXIT)
            if point == "settlement_after_claim_before_prepare":
                os._exit(CHILD_CRASH_EXIT)
            request = claim.command.canonical_payload
            if point == "settlement_after_prepare_before_fence":
                os._exit(CHILD_CRASH_EXIT)
            permit = store.fence_settlement(claim, request, now=CRASH_AT)
            if point == "settlement_after_fence_before_dispatch":
                os._exit(CHILD_CRASH_EXIT)
            _append_line(Path(provider_log), claim.command.settlement_command_id)
            if point == "settlement_during_dispatch":
                os._exit(CHILD_CRASH_EXIT)
            if point == "settlement_after_dispatch_before_outcome":
                _settled_outcome(permit)
                os._exit(CHILD_CRASH_EXIT)
            os._exit(CHILD_ERROR_EXIT)
        if point == "payment_effect_after_commit_before_claim":
            os._exit(CHILD_CRASH_EXIT)
        claim = store.claim_payment_outbox(
            worker_id="payment-effect:crashed",
            delivery_id="payment-delivery:fault-runner",
            delivery_version=1,
            now=CRASH_AT + timedelta(minutes=2),
            lease_ttl=_LEASE_TTL,
        )
        if claim is None:
            os._exit(CHILD_ERROR_EXIT)
        _append_line(Path(delivery_log), claim.message_id)
        if point == "payment_effect_after_delivery_before_receipt":
            PaymentReceipt.for_claim(
                claim,
                receipt_id=_opaque_id("crashed-payment-receipt", claim.message_id),
                delivery_reference=_opaque_id("crashed-payment-reference", claim.message_id),
                delivered_at=CRASH_AT + timedelta(minutes=2),
            )
        os._exit(CHILD_CRASH_EXIT)
    except BaseException:
        os._exit(CHILD_ERROR_EXIT)


def _spawn_crash(point: str, db_path: Path, provider_log: Path, delivery_log: Path) -> int:
    context = _process_context()
    process = context.Process(
        target=_crash_child,
        args=(point, str(db_path), str(provider_log), str(delivery_log)),
    )
    process.start()
    process.join(timeout=20)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    return process.exitcode if process.exitcode is not None else -1


def _process_fault(point: str, workdir: Path, index: int, seed: int) -> dict[str, object]:
    db_path = workdir / f"restart-{index}.db"
    provider_log = workdir / f"restart-{index}-provider.log"
    delivery_log = workdir / f"restart-{index}-delivery.log"
    store = SQLiteFollowupUnitOfWork.open(db_path)
    event_bundle = None
    if point.startswith("handoff_"):
        store.open_handoff(
            _handoff_request(seed=seed, index=index),
            HandoffEffectPolicy.default_email_disabled(),
        )
    elif point == "payment_before_evidence_claim":
        event_bundle = _payment_before_claim(store, seed=seed, index=index)
    elif point.startswith("settlement_"):
        _payment_queued(store, seed=seed, index=index)
    else:
        _payment_paid(
            store,
            seed=seed,
            index=index,
            provider_log=provider_log,
        )
    store.close()
    provider_setup = _line_count(provider_log)
    delivery_setup = _line_count(delivery_log)
    exit_code = _spawn_crash(point, db_path, provider_log, delivery_log)
    provider_after = _line_count(provider_log)
    delivery_after = _line_count(delivery_log)
    reopened = SQLiteFollowupUnitOfWork.open(db_path)
    try:
        if point.startswith("handoff_"):
            now = (
                CRASH_AT
                if point == "handoff_after_commit_before_claim"
                else CRASH_AT + timedelta(seconds=31)
            )
            HandoffOutboxWorker(
                store=reopened,
                delivery=_LoggingHandoffDelivery(delivery_log, now),
                worker_id="handoff:restart",
                lease_ttl=_LEASE_TTL,
            ).run_once(now=now)
        elif point == "payment_before_evidence_claim":
            state, event, revision, _ = event_bundle
            reopened.claim_payment_evidence(state.subject.payment_id, revision, event)
        elif point.startswith("settlement_"):
            recovery_at = CRASH_AT + timedelta(seconds=30)
            PaymentReconciler(store=reopened).run_once(now=recovery_at)
            if point in {
                "settlement_after_claim_before_prepare",
                "settlement_after_prepare_before_fence",
            }:
                PaymentSettlementWorker(
                    store=reopened,
                    settlement=_LoggingSettlementPort(provider_log),
                    worker_id="settlement:restart",
                    lease_ttl=_LEASE_TTL,
                ).run_once(now=recovery_at + timedelta(seconds=1))
        else:
            now = (
                CRASH_AT + timedelta(minutes=2)
                if point == "payment_effect_after_commit_before_claim"
                else CRASH_AT + timedelta(minutes=2, seconds=31)
            )
            PaymentOutboxWorker(
                store=reopened,
                delivery=_LoggingPaymentDelivery(delivery_log, now),
                worker_id="payment-effect:restart",
                lease_ttl=_LEASE_TTL,
            ).run_once(now=now)
        return _snapshot(
            reopened,
            point=point,
            mechanism="process_crash",
            child_exit_code=exit_code,
            provider_log=provider_log,
            delivery_log=delivery_log,
            provider_setup=provider_setup,
            provider_after_child=provider_after,
            delivery_setup=delivery_setup,
            delivery_after_child=delivery_after,
            rollback_verified=None,
        )
    finally:
        reopened.close()


def run_fault_matrix(*, seed: int, workdir: Path) -> dict[str, object]:
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, point in enumerate(FAULT_POINTS):
        row = (
            _transaction_fault(point, workdir, index, seed)
            if point in _TRANSACTION_POINTS
            else _process_fault(point, workdir, index, seed)
        )
        row["schedule"] = index
        row["violations"] = list(_fault_violations(row))
        rows.append(row)
    violations = sum(len(row["violations"]) for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "fault-matrix",
        "configuration": {"seed": seed, "fault_point_count": len(FAULT_POINTS)},
        "fault_points": list(FAULT_POINTS),
        "result": "passed" if violations == 0 else "failed",
        "violations": violations,
        "schedules": rows,
    }


def run_restart_schedules(
    *,
    seed: int,
    schedules: int,
    workdir: Path,
) -> dict[str, object]:
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if type(schedules) is not int or schedules < 1:
        raise ValueError("schedules must be an integer >= 1")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    points = list(_RESTART_POINTS)
    random.Random(seed).shuffle(points)
    rows = []
    for index in range(schedules):
        point = points[index % len(points)]
        row = _process_fault(point, workdir, index, seed)
        row["schedule"] = index
        row["violations"] = list(_fault_violations(row))
        rows.append(row)
    violations = sum(len(row["violations"]) for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "restart-schedules",
        "configuration": {"seed": seed, "schedules": schedules},
        "fault_points": list(_RESTART_POINTS),
        "fault_point_counts": {
            point: sum(row["fault_point"] == point for row in rows)
            for point in _RESTART_POINTS
        },
        "result": "passed" if violations == 0 else "failed",
        "violations": violations,
        "schedules": rows,
    }


def _retry_locked(callable_):
    for attempt in range(40):
        try:
            return callable_()
        except StoreUnavailable:
            if attempt == 39:
                raise
            time.sleep(0.005)


def _contention_child(
    domain: str,
    db_path: str,
    barrier,
    result_queue,
    side: int,
    seed: int,
    round_index: int,
    payload,
) -> None:
    try:
        store = SQLiteFollowupUnitOfWork.open(Path(db_path))
        barrier.wait(timeout=15)
        winner = 0
        token = None
        if domain == "handoff_incident":
            incident = payload
            request = _handoff_request(
                seed=seed,
                index=round_index * 10 + side,
                handoff_id=_opaque_id("contended-handoff", seed, round_index, side),
                incident_key=incident,
            )
            try:
                _retry_locked(
                    lambda: store.open_handoff(
                        request,
                        HandoffEffectPolicy.default_email_disabled(),
                    )
                )
                winner = 1
                token = 1
            except (IdentityConflict, ConcurrencyConflict):
                pass
        elif domain == "payment_command":
            claim = _retry_locked(
                lambda: store.claim_settlement(
                    worker_id=f"settlement:contender:{side}",
                    now=AT,
                    lease_ttl=_LEASE_TTL,
                )
            )
            if claim is not None:
                winner = 1
                token = claim.lease.fencing_token
        elif domain == "global_evidence_claim":
            state, event, revision = payload[side]
            try:
                _retry_locked(
                    lambda: store.claim_payment_evidence(
                        state.subject.payment_id,
                        revision,
                        event,
                    )
                )
                winner = 1
                token = 1
            except (IdentityConflict, ConcurrencyConflict):
                pass
        elif domain == "payment_outbox":
            claim = _retry_locked(
                lambda: store.claim_payment_outbox(
                    worker_id=f"payment-effect:contender:{side}",
                    delivery_id="payment-delivery:contention",
                    delivery_version=1,
                    now=AT + timedelta(minutes=2),
                    lease_ttl=_LEASE_TTL,
                )
            )
            if claim is not None:
                winner = 1
                token = claim.fencing_token
        else:
            raise ValueError("unknown contention domain")
        result_queue.put({"winner": winner, "token": token, "error": None})
        store.close()
    except BaseException as exc:
        result_queue.put(
            {"winner": 0, "token": None, "error": type(exc).__name__}
        )


def _collect_process_results(processes, result_queue) -> tuple[list[dict], list[int]]:
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
    exits = []
    for process in processes:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        exits.append(process.exitcode if process.exitcode is not None else -1)
    results = []
    for _ in processes:
        try:
            results.append(result_queue.get(timeout=5))
        except queue.Empty:
            results.append({"winner": 0, "token": None, "error": "missing_result"})
    result_queue.close()
    result_queue.join_thread()
    return results, exits


def _contention_violations(row: dict[str, object]) -> tuple[str, ...]:
    violations = []
    if row.get("domain") not in CONTENTION_DOMAINS:
        violations.append("unknown_domain")
    if row.get("winners") != 1:
        violations.append("winner_count")
    if row.get("winning_tokens") != [1]:
        violations.append("winner_token")
    if row.get("provider_delta") != 0:
        violations.append("provider_delta")
    if row.get("partial_transactions") != 0:
        violations.append("partial_transaction")
    if row.get("child_errors") != 0:
        violations.append("child_error")
    if row.get("nonzero_child_exits") != 0:
        violations.append("child_exit")
    return tuple(violations)


def _contention_round(
    *,
    domain: str,
    seed: int,
    round_index: int,
    workdir: Path,
) -> dict[str, object]:
    db_path = workdir / f"{domain}-{round_index}.db"
    provider_log = workdir / f"{domain}-{round_index}-provider.log"
    store = SQLiteFollowupUnitOfWork.open(db_path)
    payload = None
    if domain == "handoff_incident":
        payload = _opaque_id("contended-incident", seed, round_index)
    elif domain == "payment_command":
        _payment_queued(store, seed=seed, index=round_index * 10 + 1)
    elif domain == "global_evidence_claim":
        identity = _opaque_id("contended-proof", seed, round_index)
        bundles = []
        for side in (0, 1):
            state, event, revision, _ = _payment_before_claim(
                store,
                seed=seed,
                index=round_index * 10 + side,
                evidence_identity=identity,
            )
            bundles.append((state, event, revision))
        payload = tuple(bundles)
    elif domain == "payment_outbox":
        _payment_paid(
            store,
            seed=seed,
            index=round_index * 10 + 1,
            provider_log=provider_log,
        )
        setup_delivery_log = workdir / f"{domain}-{round_index}-setup-delivery.log"
        PaymentOutboxWorker(
            store=store,
            delivery=_LoggingPaymentDelivery(
                setup_delivery_log,
                AT + timedelta(minutes=1),
            ),
            worker_id="payment-effect:setup",
            lease_ttl=_LEASE_TTL,
        ).run_once(now=AT + timedelta(minutes=1))
    else:
        store.close()
        raise ValueError("unknown contention domain")
    store.close()
    provider_baseline = _line_count(provider_log)
    context = _process_context()
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_contention_child,
            args=(
                domain,
                str(db_path),
                barrier,
                result_queue,
                side,
                seed,
                round_index,
                payload,
            ),
        )
        for side in (0, 1)
    ]
    results, exits = _collect_process_results(processes, result_queue)
    reopened = SQLiteFollowupUnitOfWork.open(db_path)
    try:
        partial = _partial_transaction_count(reopened)
    finally:
        reopened.close()
    return {
        "domain": domain,
        "round": round_index,
        "winners": sum(result["winner"] for result in results),
        "winning_tokens": sorted(
            result["token"] for result in results if result["winner"]
        ),
        "provider_delta": _line_count(provider_log) - provider_baseline,
        "partial_transactions": partial,
        "child_errors": sum(result["error"] is not None for result in results),
        "child_error_types": sorted(
            result["error"] for result in results if result["error"] is not None
        ),
        "nonzero_child_exits": sum(exit_code != 0 for exit_code in exits),
    }


def run_contention(*, seed: int, rounds: int, workdir: Path) -> dict[str, object]:
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if type(rounds) is not int or rounds < 1:
        raise ValueError("rounds must be an integer >= 1")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for round_index in range(rounds):
        for domain in CONTENTION_DOMAINS:
            row = _contention_round(
                domain=domain,
                seed=seed,
                round_index=round_index,
                workdir=workdir,
            )
            row["violations"] = list(_contention_violations(row))
            rows.append(row)
    violations = sum(len(row["violations"]) for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "multiprocess-contention",
        "configuration": {"seed": seed, "rounds": rounds},
        "domains": list(CONTENTION_DOMAINS),
        "domain_rounds": {
            domain: sum(row["domain"] == domain for row in rows)
            for domain in CONTENTION_DOMAINS
        },
        "domain_winners": {
            domain: sum(row["winners"] for row in rows if row["domain"] == domain)
            for domain in CONTENTION_DOMAINS
        },
        "result": "passed" if violations == 0 else "failed",
        "violations": violations,
        "round_results": rows,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=2026071906)
    parser.add_argument("--restart-schedules", type=int, required=True)
    parser.add_argument("--contention-rounds", type=int, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--write-fault-matrix", type=Path, required=True)
    parser.add_argument("--write-restart", type=Path, required=True)
    parser.add_argument("--write-concurrency", type=Path, required=True)
    args = parser.parse_args(argv)
    minimum_restarts = (
        SMOKE_RESTART_SCHEDULES if args.smoke else MINIMUM_RESTART_SCHEDULES
    )
    minimum_rounds = (
        SMOKE_CONTENTION_ROUNDS if args.smoke else MINIMUM_CONTENTION_ROUNDS
    )
    errors = []
    if args.restart_schedules < minimum_restarts:
        errors.append(f"restart-schedules must be at least {minimum_restarts}")
    if args.contention_rounds < minimum_rounds:
        errors.append(f"contention-rounds must be at least {minimum_rounds}")
    if errors:
        parser.error("; ".join(errors))
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="phase6-fault-runner-") as directory:
        root = Path(directory)
        fault_matrix = run_fault_matrix(
            seed=args.seed,
            workdir=root / "fault-matrix",
        )
        restart = run_restart_schedules(
            seed=args.seed,
            schedules=args.restart_schedules,
            workdir=root / "restart",
        )
        concurrency = run_contention(
            seed=args.seed,
            rounds=args.contention_rounds,
            workdir=root / "concurrency",
        )
    _write_json(args.write_fault_matrix, fault_matrix)
    _write_json(args.write_restart, restart)
    _write_json(args.write_concurrency, concurrency)
    return 0 if all(
        report["result"] == "passed"
        for report in (fault_matrix, restart, concurrency)
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
