#!/usr/bin/env python3
"""Deterministic local fault, restart, and multiprocess contention runner."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import queue
import random
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_domain import ExecutionCertainty, dumps_command
from reservation_execution import DeliveryReceipt, DispatchRequest
from reservation_execution.outbox import OutboxWorker
from reservation_execution.reconciliation import Reconciler
from reservation_execution.sqlite_store import SQLiteUnitOfWork, StoreError
from reservation_execution.worker import CommandWorker
from tests.phase5_helpers import T0, persist_script, workflow_events

SCHEMA_VERSION = 1
PHASE = "phase-05-durable-command-execution"
MINIMUM_RESTART_SCHEDULES = 2_000
MINIMUM_CONTENTION_ROUNDS = 50
SMOKE_RESTART_SCHEDULES = 8
SMOKE_CONTENTION_ROUNDS = 2
COMMAND_AT = T0 + timedelta(minutes=10)
DELIVERY_AT = T0 + timedelta(minutes=11)
LEASE_TTL = timedelta(seconds=30)
CHILD_CRASH_EXIT = 91
CHILD_ERROR_EXIT = 92

FAULT_POINTS = (
    "before_event",
    "after_event_before_state",
    "after_state_before_command",
    "after_command_before_ledger",
    "after_ledger_before_commit",
    "after_commit_before_claim",
    "after_claim_before_prepare",
    "during_prepare",
    "after_prepare_before_fence",
    "after_fence_before_dispatch",
    "during_dispatch",
    "after_dispatch_before_outcome",
    "after_outcome_before_state",
    "after_state_before_outbox",
    "after_outbox_before_commit",
    "during_delivery",
    "after_delivery_before_receipt",
)

_TRANSACTION_POINTS = (
    "before_event",
    "after_event_before_state",
    "after_state_before_command",
    "after_command_before_ledger",
    "after_ledger_before_commit",
    "after_outcome_before_state",
    "after_state_before_outbox",
    "after_outbox_before_commit",
)
_RESTART_POINTS = tuple(point for point in FAULT_POINTS if point not in _TRANSACTION_POINTS)
_CREATION_TRIGGER_SPECS = {
    "before_event": ("BEFORE", "INSERT", "domain_events"),
    "after_event_before_state": ("BEFORE", "UPDATE", "workflows"),
    "after_state_before_command": ("BEFORE", "INSERT", "reservation_commands"),
    "after_command_before_ledger": ("BEFORE", "INSERT", "execution_ledger"),
    "after_ledger_before_commit": ("AFTER", "INSERT", "execution_ledger"),
}
_OUTCOME_TRIGGER_SPECS = {
    "after_outcome_before_state": ("BEFORE", "UPDATE", "workflows"),
    "after_state_before_outbox": ("BEFORE", "INSERT", "outbox_messages"),
    "after_outbox_before_commit": ("AFTER", "INSERT", "outbox_messages"),
}


def _opaque_id(prefix: str, *parts: object) -> str:
    material = "|".join(str(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


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
    """Prefer cheap isolated forks on Linux; retain a spawn fallback."""

    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return multiprocessing.get_context("spawn")


def _receipt(message, delivered_at: datetime) -> DeliveryReceipt:
    reference = "delivery:fault-runner"
    material = json.dumps(
        {
            "message_id": message.message_id,
            "delivery_reference": reference,
            "delivered_at": delivered_at.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return DeliveryReceipt(
        message_id=message.message_id,
        delivery_reference=reference,
        receipt_hash=hashlib.sha256(material.encode("utf-8")).hexdigest(),
        delivered_at=delivered_at,
    )


class _AppendingExecutionAdapter:
    adapter_id = "fault-runner-execution"
    adapter_version = 1

    def __init__(self, call_log: Path):
        self._call_log = call_log
        self._command = None

    def prepare(self, command):
        self._command = command
        return DispatchRequest.from_command(command, dumps_command(command))

    def dispatch(self, request, *, idempotency_key):
        if self._command is None:
            raise AssertionError("dispatch occurred before prepare")
        if idempotency_key != self._command.idempotency_key:
            raise AssertionError("worker changed the authorized idempotency key")
        _append_line(self._call_log, self._command.command_id)
        return self._command.outcome(
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
            normalized_status="synthetic_effect_confirmed",
            provider_reference="provider:fault-runner",
            evidence=(request.payload_hash,),
        )


class _AppendingDeliveryPort:
    delivery_id = "fault-runner-delivery"
    delivery_version = 1

    def __init__(self, call_log: Path, delivered_at: datetime):
        self._call_log = call_log
        self._delivered_at = delivered_at

    def deliver(self, message):
        _append_line(self._call_log, message.message_id)
        return _receipt(message, self._delivered_at)


def _setup_queued(path: Path, label: str) -> tuple[str, str]:
    workflow_id = _opaque_id("workflow", "phase5-fault", label)
    store = SQLiteUnitOfWork.open(path)
    try:
        initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
        store.create_workflow(initial)
        persist_script(store, workflow_id, script)
        command_id = store.load_workflow(workflow_id).command.command_id
        store.assert_execution_consistency()
        return workflow_id, command_id
    finally:
        store.close()


def _setup_before_command(path: Path, label: str):
    workflow_id = _opaque_id("workflow", "phase5-transaction", label)
    store = SQLiteUnitOfWork.open(path)
    initial, script = workflow_events("cloudbeds", workflow_id=workflow_id)
    store.create_workflow(initial)
    persist_script(store, workflow_id, script[:-1])
    return store, workflow_id, script[-1]


def _setup_fenced(path: Path, label: str):
    workflow_id, command_id = _setup_queued(path, label)
    store = SQLiteUnitOfWork.open(path)
    claim = store.claim_command(
        worker_id="worker:fault-runner",
        now=COMMAND_AT,
        lease_ttl=LEASE_TTL,
    )
    request = DispatchRequest.from_command(claim.command, dumps_command(claim.command))
    permit = store.fence_dispatch(claim, request, now=COMMAND_AT)
    return store, workflow_id, command_id, permit, request


def _setup_outbox(path: Path, label: str) -> tuple[str, str]:
    workflow_id, command_id = _setup_queued(path, label)
    setup_log = path.with_name(path.stem + "-setup-provider.log")
    store = SQLiteUnitOfWork.open(path)
    try:
        worker = CommandWorker(
            store=store,
            adapter=_AppendingExecutionAdapter(setup_log),
            worker_id="worker:outbox-setup",
            lease_ttl=LEASE_TTL,
        )
        worker.run_once(now=COMMAND_AT)
        store.assert_execution_consistency()
        return workflow_id, command_id
    finally:
        store.close()


def _install_abort_trigger(
    store: SQLiteUnitOfWork,
    *,
    timing: str,
    operation: str,
    table: str,
) -> None:
    allowed = {"BEFORE", "AFTER", "INSERT", "UPDATE"}
    if timing not in allowed or operation not in allowed:
        raise ValueError("invalid static trigger specification")
    if table not in {
        "domain_events",
        "workflows",
        "reservation_commands",
        "execution_ledger",
        "outbox_messages",
    }:
        raise ValueError("invalid static trigger table")
    store._connection.execute(
        f"CREATE TEMP TRIGGER phase5_fault {timing} {operation} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'synthetic_phase5_fault'); END"
    )


def _partial_transaction_count(store: SQLiteUnitOfWork) -> int:
    try:
        store.assert_execution_consistency()
        if store._connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return 1
        if store._connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return 1
        return 0
    except BaseException:
        return 1


def _snapshot(
    store: SQLiteUnitOfWork,
    *,
    fault_point: str,
    mechanism: str,
    provider_log: Path,
    delivery_log: Path,
    child_exit_code: int | None,
) -> dict[str, object]:
    command_count = store._connection.execute(
        "SELECT COUNT(*) FROM reservation_commands"
    ).fetchone()[0]
    slots = store._connection.execute(
        "SELECT COALESCE(SUM(dispatch_slots_consumed), 0) FROM execution_ledger"
    ).fetchone()[0]
    provider_calls = _line_count(provider_log)
    unknown_rows = store._connection.execute(
        "SELECT COUNT(*) FROM execution_ledger WHERE outcome_json LIKE ?",
        ('%"certainty":"called_unknown"%',),
    ).fetchone()[0]
    return {
        "fault_point": fault_point,
        "mechanism": mechanism,
        "command_count": command_count,
        "dispatch_slots_consumed": slots,
        "provider_calls": provider_calls,
        "delivery_calls": _line_count(delivery_log),
        "partial_transactions": _partial_transaction_count(store),
        "called_unknown_redispatches": (
            max(0, provider_calls - 1) if unknown_rows else 0
        ),
        "child_exit_code": child_exit_code,
    }


def _schedule_violations(schedule: dict[str, object]) -> tuple[str, ...]:
    violations = []
    if schedule["command_count"] > 1:
        violations.append("second_command")
    if schedule["dispatch_slots_consumed"] > 1:
        violations.append("second_dispatch_slot")
    if schedule["provider_calls"] > 1:
        violations.append("second_provider_call")
    if schedule["partial_transactions"] != 0:
        violations.append("partial_transaction")
    if schedule["called_unknown_redispatches"] != 0:
        violations.append("called_unknown_redispatch")
    if schedule["mechanism"] == "process_crash" and schedule["child_exit_code"] != 91:
        violations.append("wrong_child_exit")
    return tuple(violations)


def _run_transaction_fault(point: str, directory: Path, index: int) -> dict[str, object]:
    db_path = directory / f"transaction-{index}.db"
    provider_log = directory / f"transaction-{index}-provider.log"
    delivery_log = directory / f"transaction-{index}-delivery.log"
    if point in _CREATION_TRIGGER_SPECS:
        store, workflow_id, (event, outbox) = _setup_before_command(
            db_path,
            f"{index}:{point}",
        )
        before_revision = store.load_workflow(workflow_id).meta.revision
        _install_abort_trigger(
            store,
            timing=_CREATION_TRIGGER_SPECS[point][0],
            operation=_CREATION_TRIGGER_SPECS[point][1],
            table=_CREATION_TRIGGER_SPECS[point][2],
        )
        failed = False
        try:
            store.apply_event(
                workflow_id,
                before_revision,
                event,
                outbox=outbox,
            )
        except StoreError:
            failed = True
        finally:
            store.close()
        reopened = SQLiteUnitOfWork.open(db_path)
        try:
            if not failed:
                reopened._connection.execute("PRAGMA user_version")
            schedule = _snapshot(
                reopened,
                fault_point=point,
                mechanism="transaction_trigger",
                provider_log=provider_log,
                delivery_log=delivery_log,
                child_exit_code=None,
            )
            if reopened.load_workflow(workflow_id).meta.revision != before_revision:
                schedule["partial_transactions"] = 1
        finally:
            reopened.close()
        if not failed:
            schedule["partial_transactions"] = 1
        return schedule

    store, _, command_id, permit, request = _setup_fenced(
        db_path,
        f"{index}:{point}",
    )
    _append_line(provider_log, command_id)
    command = store.load_command(command_id)
    outcome = command.outcome(
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        normalized_status="synthetic_effect_confirmed",
        provider_reference="provider:fault-runner",
        evidence=(request.payload_hash,),
    )
    spec = _OUTCOME_TRIGGER_SPECS[point]
    _install_abort_trigger(
        store,
        timing=spec[0],
        operation=spec[1],
        table=spec[2],
    )
    failed = False
    try:
        store.record_outcome(permit, outcome, now=COMMAND_AT + timedelta(seconds=1))
    except StoreError:
        failed = True
    finally:
        store.close()
    reopened = SQLiteUnitOfWork.open(db_path)
    try:
        Reconciler(reopened).run_once(now=COMMAND_AT + timedelta(seconds=31))
        schedule = _snapshot(
            reopened,
            fault_point=point,
            mechanism="transaction_trigger",
            provider_log=provider_log,
            delivery_log=delivery_log,
            child_exit_code=None,
        )
    finally:
        reopened.close()
    if not failed:
        schedule["partial_transactions"] = 1
    return schedule


def _crash_child(
    point: str,
    db_path: str,
    provider_log: str,
    delivery_log: str,
) -> None:
    try:
        store = SQLiteUnitOfWork.open(Path(db_path))
        if point == "after_commit_before_claim":
            os._exit(CHILD_CRASH_EXIT)
        if point in {"during_delivery", "after_delivery_before_receipt"}:
            claim = store.claim_outbox(
                worker_id="delivery:crashed",
                now=DELIVERY_AT,
                lease_ttl=LEASE_TTL,
            )
            if claim is None:
                os._exit(CHILD_ERROR_EXIT)
            _append_line(Path(delivery_log), claim.message.message_id)
            if point == "after_delivery_before_receipt":
                _receipt(claim.message, DELIVERY_AT)
            os._exit(CHILD_CRASH_EXIT)
        claim = store.claim_command(
            worker_id="worker:crashed",
            now=COMMAND_AT,
            lease_ttl=LEASE_TTL,
        )
        if claim is None:
            os._exit(CHILD_ERROR_EXIT)
        if point == "after_claim_before_prepare":
            os._exit(CHILD_CRASH_EXIT)
        if point == "during_prepare":
            dumps_command(claim.command)
            os._exit(CHILD_CRASH_EXIT)
        request = DispatchRequest.from_command(
            claim.command,
            dumps_command(claim.command),
        )
        if point == "after_prepare_before_fence":
            os._exit(CHILD_CRASH_EXIT)
        store.fence_dispatch(claim, request, now=COMMAND_AT)
        if point == "after_fence_before_dispatch":
            os._exit(CHILD_CRASH_EXIT)
        _append_line(Path(provider_log), claim.command.command_id)
        if point == "during_dispatch":
            os._exit(CHILD_CRASH_EXIT)
        if point == "after_dispatch_before_outcome":
            claim.command.outcome(
                certainty=ExecutionCertainty.EFFECT_CONFIRMED,
                normalized_status="synthetic_effect_confirmed",
                provider_reference="provider:fault-runner",
                evidence=(request.payload_hash,),
            )
            os._exit(CHILD_CRASH_EXIT)
        os._exit(CHILD_ERROR_EXIT)
    except BaseException:
        os._exit(CHILD_ERROR_EXIT)


def _spawn_crash(
    point: str,
    db_path: Path,
    provider_log: Path,
    delivery_log: Path,
) -> int:
    context = _process_context()
    child = context.Process(
        target=_crash_child,
        args=(point, str(db_path), str(provider_log), str(delivery_log)),
    )
    child.start()
    child.join(timeout=20)
    if child.is_alive():
        child.kill()
        child.join(timeout=5)
    return child.exitcode if child.exitcode is not None else -1


def _run_restart_fault(point: str, directory: Path, index: int) -> dict[str, object]:
    db_path = directory / f"restart-{index}.db"
    provider_log = directory / f"restart-{index}-provider.log"
    delivery_log = directory / f"restart-{index}-delivery.log"
    label = f"{index}:{point}"
    if point in {"during_delivery", "after_delivery_before_receipt"}:
        _setup_outbox(db_path, label)
    else:
        _setup_queued(db_path, label)
    exit_code = _spawn_crash(point, db_path, provider_log, delivery_log)
    store = SQLiteUnitOfWork.open(db_path)
    try:
        if point in {"during_delivery", "after_delivery_before_receipt"}:
            delivery_at = DELIVERY_AT + timedelta(seconds=31)
            worker = OutboxWorker(
                store=store,
                delivery=_AppendingDeliveryPort(delivery_log, delivery_at),
                worker_id="delivery:restart",
                lease_ttl=LEASE_TTL,
            )
            worker.run_once(now=delivery_at)
        elif point == "after_commit_before_claim":
            worker = CommandWorker(
                store=store,
                adapter=_AppendingExecutionAdapter(provider_log),
                worker_id="worker:restart",
                lease_ttl=LEASE_TTL,
            )
            worker.run_once(now=COMMAND_AT)
        elif point in {
            "after_claim_before_prepare",
            "during_prepare",
            "after_prepare_before_fence",
        }:
            Reconciler(store).run_once(now=COMMAND_AT + timedelta(seconds=30))
            worker = CommandWorker(
                store=store,
                adapter=_AppendingExecutionAdapter(provider_log),
                worker_id="worker:restart",
                lease_ttl=LEASE_TTL,
            )
            worker.run_once(now=COMMAND_AT + timedelta(seconds=31))
        else:
            Reconciler(store).run_once(now=COMMAND_AT + timedelta(seconds=30))
        return _snapshot(
            store,
            fault_point=point,
            mechanism="process_crash",
            provider_log=provider_log,
            delivery_log=delivery_log,
            child_exit_code=exit_code,
        )
    finally:
        store.close()


def run_fault_matrix(*, seed: int, workdir: Path) -> dict[str, object]:
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    schedules = []
    for index, point in enumerate(FAULT_POINTS):
        if point in _TRANSACTION_POINTS:
            schedule = _run_transaction_fault(point, workdir, index)
        else:
            schedule = _run_restart_fault(point, workdir, index)
        schedule["schedule"] = index
        schedule["violations"] = list(_schedule_violations(schedule))
        schedules.append(schedule)
    violations = sum(len(item["violations"]) for item in schedules)
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "fault-matrix",
        "configuration": {"seed": seed, "fault_point_count": len(FAULT_POINTS)},
        "fault_points": list(FAULT_POINTS),
        "result": "passed" if violations == 0 else "failed",
        "violations": violations,
        "schedules": schedules,
    }


def run_restart_schedules(
    *,
    seed: int,
    schedules: int,
    workdir: Path,
) -> dict[str, object]:
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    if type(schedules) is not int or schedules < 1:
        raise ValueError("schedules must be an integer >= 1")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    generator = random.Random(seed)
    results = []
    for index in range(schedules):
        point = _RESTART_POINTS[generator.randrange(len(_RESTART_POINTS))]
        schedule = _run_restart_fault(point, workdir, index)
        schedule["schedule"] = index
        schedule["violations"] = list(_schedule_violations(schedule))
        results.append(schedule)
    violations = sum(len(item["violations"]) for item in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "restart-schedules",
        "configuration": {"seed": seed, "schedules": schedules},
        "result": "passed" if violations == 0 else "failed",
        "violations": violations,
        "fault_point_counts": {
            point: sum(item["fault_point"] == point for item in results)
            for point in _RESTART_POINTS
        },
        "schedules": results,
    }


def _command_contender(
    db_path: str,
    call_log: str,
    barrier,
    result_queue,
    worker_id: str,
) -> None:
    try:
        store = SQLiteUnitOfWork.open(Path(db_path))
        barrier.wait(timeout=15)
        claim = store.claim_command(
            worker_id=worker_id,
            now=COMMAND_AT,
            lease_ttl=LEASE_TTL,
        )
        if claim is None:
            result_queue.put({"winner": 0, "token": None, "error": None})
        else:
            request = DispatchRequest.from_command(
                claim.command,
                dumps_command(claim.command),
            )
            store.fence_dispatch(claim, request, now=COMMAND_AT)
            _append_line(Path(call_log), claim.command.command_id)
            result_queue.put(
                {
                    "winner": 1,
                    "token": claim.lease.fencing_token,
                    "error": None,
                }
            )
        store.close()
    except BaseException as exc:
        result_queue.put(
            {"winner": 0, "token": None, "error": type(exc).__name__}
        )


def _outbox_contender(
    db_path: str,
    barrier,
    result_queue,
    worker_id: str,
) -> None:
    try:
        store = SQLiteUnitOfWork.open(Path(db_path))
        barrier.wait(timeout=15)
        claim = store.claim_outbox(
            worker_id=worker_id,
            now=DELIVERY_AT,
            lease_ttl=LEASE_TTL,
        )
        result_queue.put(
            {
                "winner": int(claim is not None),
                "token": None if claim is None else claim.lease.fencing_token,
                "error": None,
            }
        )
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
    exit_codes = []
    for process in processes:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        exit_codes.append(process.exitcode if process.exitcode is not None else -1)
    results = []
    for _ in processes:
        try:
            results.append(result_queue.get(timeout=5))
        except queue.Empty:
            results.append(
                {"winner": 0, "token": None, "error": "missing_result"}
            )
    result_queue.close()
    result_queue.join_thread()
    return results, exit_codes


def _command_contention_round(directory: Path, round_index: int) -> dict[str, object]:
    db_path = directory / f"command-{round_index}.db"
    call_log = directory / f"command-{round_index}-provider.log"
    _setup_queued(db_path, f"command-contention:{round_index}")
    context = _process_context()
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_command_contender,
            args=(str(db_path), str(call_log), barrier, result_queue, f"worker:{side}"),
        )
        for side in ("one", "two")
    ]
    results, exit_codes = _collect_process_results(processes, result_queue)
    store = SQLiteUnitOfWork.open(db_path)
    try:
        Reconciler(store).run_once(now=COMMAND_AT + timedelta(seconds=30))
        partial = _partial_transaction_count(store)
    finally:
        store.close()
    return {
        "kind": "command",
        "round": round_index,
        "winners": sum(item["winner"] for item in results),
        "winning_tokens": sorted(
            item["token"] for item in results if item["winner"] == 1
        ),
        "provider_calls": _line_count(call_log),
        "partial_transactions": partial,
        "child_errors": sum(item["error"] is not None for item in results),
        "nonzero_child_exits": sum(code != 0 for code in exit_codes),
    }


def _outbox_contention_round(directory: Path, round_index: int) -> dict[str, object]:
    db_path = directory / f"outbox-{round_index}.db"
    _setup_outbox(db_path, f"outbox-contention:{round_index}")
    blocker = SQLiteUnitOfWork.open(db_path)
    try:
        blocker.claim_outbox(
            worker_id="delivery:blocker",
            now=DELIVERY_AT - timedelta(seconds=1),
            lease_ttl=timedelta(minutes=2),
        )
    finally:
        blocker.close()
    context = _process_context()
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_outbox_contender,
            args=(str(db_path), barrier, result_queue, f"delivery:{side}"),
        )
        for side in ("one", "two")
    ]
    results, exit_codes = _collect_process_results(processes, result_queue)
    store = SQLiteUnitOfWork.open(db_path)
    try:
        partial = _partial_transaction_count(store)
    finally:
        store.close()
    return {
        "kind": "outbox",
        "round": round_index,
        "winners": sum(item["winner"] for item in results),
        "winning_tokens": sorted(
            item["token"] for item in results if item["winner"] == 1
        ),
        "provider_calls": 0,
        "partial_transactions": partial,
        "child_errors": sum(item["error"] is not None for item in results),
        "nonzero_child_exits": sum(code != 0 for code in exit_codes),
    }


def _contention_violations(result: dict[str, object]) -> tuple[str, ...]:
    violations = []
    if result["winners"] != 1:
        violations.append("claim_winner_count")
    if result["winning_tokens"] != [1]:
        violations.append("claim_winner_token")
    if result["provider_calls"] > 1:
        violations.append("second_provider_call")
    if result["partial_transactions"] != 0:
        violations.append("partial_transaction")
    if result["child_errors"] != 0 or result["nonzero_child_exits"] != 0:
        violations.append("child_failure")
    return tuple(violations)


def run_contention(*, seed: int, rounds: int, workdir: Path) -> dict[str, object]:
    if type(seed) is not int:
        raise TypeError("seed must be an integer")
    if type(rounds) is not int or rounds < 1:
        raise ValueError("rounds must be an integer >= 1")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    results = []
    for round_index in range(rounds):
        results.append(_command_contention_round(workdir, round_index))
        results.append(_outbox_contention_round(workdir, round_index))
    for result in results:
        result["violations"] = list(_contention_violations(result))
    violations = sum(len(item["violations"]) for item in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "kind": "multiprocess-contention",
        "configuration": {"seed": seed, "rounds": rounds},
        "result": "passed" if violations == 0 else "failed",
        "violations": violations,
        "command_rounds": rounds,
        "outbox_rounds": rounds,
        "command_claim_winners": sum(
            item["winners"] for item in results if item["kind"] == "command"
        ),
        "outbox_claim_winners": sum(
            item["winners"] for item in results if item["kind"] == "outbox"
        ),
        "max_provider_calls_per_round": max(
            (item["provider_calls"] for item in results),
            default=0,
        ),
        "partial_transactions": sum(item["partial_transactions"] for item in results),
        "round_results": results,
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
    parser.add_argument("--seed", type=int, default=2026071905)
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
    with tempfile.TemporaryDirectory(prefix="phase5-fault-runner-") as directory:
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
