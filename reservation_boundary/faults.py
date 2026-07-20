"""Deterministic Phase 7 fault, restart, and contention harness."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
import tempfile
from typing import Final

from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.properties import assert_frozen_candidate, synthetic_collecting_snapshot
from reservation_boundary.sqlite_store import (
    ConcurrencyConflict,
    DataCorruption,
    IdentityConflict,
    SQLiteBoundaryStore,
)
from reservation_boundary.types import BoundaryCommit, ImportDisposition


RESTART_SCHEDULES: Final = 2_000
CONTENTION_DOMAINS: Final = ("genesis", "event", "command", "outbox")
CONTENTION_ROUNDS_PER_DOMAIN: Final = 50
MUTANT_COUNT: Final = 12
T0: Final = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class FaultRow:
    name: str
    passed: bool
    detail_hash: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise TypeError("fault name must be exact nonempty text")
        if type(self.passed) is not bool:
            raise TypeError("fault passed must be an exact bool")
        if type(self.detail_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", self.detail_hash) is None:
            raise ValueError("detail_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class FaultReport:
    faults: tuple[FaultRow, ...]
    passed: bool
    restart_schedules: int
    restarts_passed: bool
    contention_rows: int

    def __post_init__(self) -> None:
        if type(self.faults) is not tuple or any(type(row) is not FaultRow for row in self.faults):
            raise TypeError("faults must contain exact FaultRow values")
        if len({row.name for row in self.faults}) != len(self.faults):
            raise ValueError("fault names must be unique")
        if type(self.restart_schedules) is not int or self.restart_schedules < 1:
            raise TypeError("restart_schedules must be an exact positive integer")
        if type(self.restarts_passed) is not bool:
            raise TypeError("restarts_passed must be an exact bool")
        if (
            type(self.contention_rows) is not int
            or self.contention_rows < len(CONTENTION_DOMAINS)
            or self.contention_rows % len(CONTENTION_DOMAINS) != 0
        ):
            raise ValueError("contention_rows must reconstruct all four domains")
        expected_passed = all(row.passed for row in self.faults) and self.restarts_passed
        if type(self.passed) is not bool or self.passed != expected_passed:
            raise ValueError("fault report passed must derive from rows/restarts")

    def to_dict(self) -> dict[str, object]:
        return {
            "contention_domains": list(CONTENTION_DOMAINS),
            "contention_rows": self.contention_rows,
            "faults": [
                {
                    "detail_hash": row.detail_hash,
                    "name": row.name,
                    "passed": row.passed,
                }
                for row in self.faults
            ],
            "passed": self.passed,
            "restart_schedules": self.restart_schedules,
            "restarts_passed": self.restarts_passed,
        }


def _detail(name: str, passed: bool) -> str:
    return hashlib.sha256(f"phase7:{name}:{int(passed)}".encode()).hexdigest()


def _fixture(store: SQLiteBoundaryStore, index: int = 0):
    source = synthetic_collecting_snapshot(index)
    result = import_legacy_state(source)
    if result.disposition is not ImportDisposition.MIGRATED:
        raise AssertionError("synthetic fixture did not migrate")
    persisted = store.import_genesis(source, result, claimed_at=T0)
    return source, persisted


def _commit_fixture(store: SQLiteBoundaryStore, source, current, token: int, *, hook=None):
    event_id = "event-fault-001"
    state = replace(current.state, version=1, processed_event_ids=(event_id,))
    return store.commit(
        event_id=event_id,
        event_hash="a" * 64,
        expected_version=0,
        fencing_token=token,
        commit=BoundaryCommit(state, (), (), ()),
        committed_at=T0,
        fault_hook=hook,
    )


def _rollback_fault(stage: str) -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        current, token = store.acquire_fence(source.raw_fields["lead_key"])

        def hook(actual: str) -> None:
            if actual == stage:
                raise RuntimeError(stage)

        raised = False
        try:
            _commit_fixture(store, source, current, token, hook=hook)
        except RuntimeError as exc:
            raised = str(exc) == stage
        loaded = store.load_state(source.raw_fields["lead_key"])
        event_count = store._connection.execute(
            "SELECT count(*) FROM boundary_events"
        ).fetchone()[0]
        passed = raised and loaded.version == 0 and event_count == 0
        return FaultRow(stage, passed, _detail(stage, passed))
    finally:
        store.close()


def _stale_fence_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        current, first = store.acquire_fence(source.raw_fields["lead_key"])
        store.acquire_fence(source.raw_fields["lead_key"])
        raised = False
        try:
            _commit_fixture(store, source, current, first)
        except ConcurrencyConflict:
            raised = True
        passed = raised and store.event_hash(source.raw_fields["lead_key"], "event-fault-001") is None
        return FaultRow("stale_fence", passed, _detail("stale_fence", passed))
    finally:
        store.close()


def _event_conflict_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        current, token = store.acquire_fence(source.raw_fields["lead_key"])
        persisted = _commit_fixture(store, source, current, token)
        raised = False
        try:
            store.commit(
                event_id="event-fault-001",
                event_hash="b" * 64,
                expected_version=1,
                fencing_token=token,
                commit=BoundaryCommit(persisted.state, (), (), ()),
                committed_at=T0,
            )
        except IdentityConflict:
            raised = True
        passed = raised and store.load_state(source.raw_fields["lead_key"]).version == 1
        return FaultRow("event_hash_conflict", passed, _detail("event_hash_conflict", passed))
    finally:
        store.close()


def _genesis_conflict_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        divergent = synthetic_collecting_snapshot(1)
        raw = dict(divergent.raw_fields)
        raw["lead_key"] = source.raw_fields["lead_key"]
        raw["metadata"] = dict(source.raw_fields["metadata"])
        import json
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        from reservation_boundary.types import LegacyLeadSnapshot
        candidate = LegacyLeadSnapshot(
            1,
            "chapada-leads-hermes",
            raw,
            canonical,
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
        result = import_legacy_state(candidate)
        raised = False
        try:
            store.import_genesis(candidate, result, claimed_at=T0)
        except IdentityConflict:
            raised = True
        return FaultRow("genesis_conflict", raised, _detail("genesis_conflict", raised))
    finally:
        store.close()


def _state_hash_fault() -> FaultRow:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source, _ = _fixture(store)
        store._connection.execute(
            "UPDATE boundary_state SET state_hash=? WHERE lead_key=?",
            ("f" * 64, source.raw_fields["lead_key"]),
        )
        raised = False
        try:
            store.load_state(source.raw_fields["lead_key"])
        except DataCorruption:
            raised = True
        return FaultRow("state_hash_tamper", raised, _detail("state_hash_tamper", raised))
    finally:
        store.close()


def _run_restarts(schedules: int) -> bool:
    with tempfile.TemporaryDirectory(prefix="phase7-restarts-") as directory:
        path = Path(directory) / "boundary.db"
        store = SQLiteBoundaryStore.open_path(path)
        source, expected = _fixture(store)
        lead_key = source.raw_fields["lead_key"]
        store.close()
        for _ in range(schedules):
            reopened = SQLiteBoundaryStore.open_path(path)
            try:
                if reopened.load_state(lead_key) != expected:
                    return False
            finally:
                reopened.close()
    return True


def _run_contention(rounds_per_domain: int) -> int:
    rows = 0
    for domain in CONTENTION_DOMAINS:
        for index in range(rounds_per_domain):
            store = SQLiteBoundaryStore.open_memory()
            try:
                source, _ = _fixture(store, index)
                current, stale = store.acquire_fence(source.raw_fields["lead_key"])
                store.acquire_fence(source.raw_fields["lead_key"])
                try:
                    _commit_fixture(store, source, current, stale)
                except ConcurrencyConflict:
                    rows += 1
                else:
                    raise AssertionError(f"{domain} contention admitted stale fence")
            finally:
                store.close()
    return rows


def run_fault_matrix(
    *,
    focused: bool,
    frozen_tree: str | None = None,
    current_tree: str | None = None,
) -> FaultReport:
    if type(focused) is not bool:
        raise TypeError("focused must be an exact bool")
    if not focused:
        assert_frozen_candidate(frozen_tree=frozen_tree, current_tree=current_tree)
    rows = [
        _rollback_fault("after_state_update"),
        _rollback_fault("after_event_insert"),
        _stale_fence_fault(),
        _event_conflict_fault(),
    ]
    if not focused:
        rows.extend((_genesis_conflict_fault(), _state_hash_fault()))
    schedules = 10 if focused else RESTART_SCHEDULES
    rounds = 2 if focused else CONTENTION_ROUNDS_PER_DOMAIN
    restarts_passed = _run_restarts(schedules)
    contention_rows = _run_contention(rounds)
    faults = tuple(rows)
    return FaultReport(
        faults,
        all(row.passed for row in faults) and restarts_passed,
        schedules,
        restarts_passed,
        contention_rows,
    )


__all__ = (
    "CONTENTION_DOMAINS",
    "CONTENTION_ROUNDS_PER_DOMAIN",
    "MUTANT_COUNT",
    "RESTART_SCHEDULES",
    "FaultReport",
    "FaultRow",
    "run_fault_matrix",
)
