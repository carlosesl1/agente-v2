"""Closed direct invariant probes for twelve Phase 7 mutation classes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Final

from reservation_domain import new_workflow

from reservation_boundary.dispatch import DispatchRejected, ToolDispatch
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.properties import T0, assert_frozen_candidate, synthetic_collecting_snapshot
from reservation_boundary.serialization import from_wire_json, to_wire_json
from reservation_boundary.shadow import DecisionObservation, compare
from reservation_boundary.sqlite_store import ConcurrencyConflict, LegacyStateReadPort, SQLiteBoundaryStore
from reservation_boundary.types import (
    ActivityReservationArguments,
    BoundaryCommit,
    BoundaryState,
    DecimalSlot,
    DivergenceSeverity,
    ImportDisposition,
    IntegerSlot,
    LegacyLeadSnapshot,
    LodgingReadArguments,
    StripeLinkArguments,
    ToolDispatchRequest,
    TurnLease,
    TurnPlanReason,
)


@dataclass(frozen=True, slots=True)
class Mutant:
    name: str
    owner: str
    probe_name: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.name, "name"),
            (self.owner, "owner"),
            (self.probe_name, "probe_name"),
        ):
            if type(value) is not str or not value:
                raise TypeError(f"{field_name} must be exact nonempty text")


@dataclass(frozen=True, slots=True)
class MutationRow:
    name: str
    killed: bool
    probe_hash: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise TypeError("name must be exact nonempty text")
        if type(self.killed) is not bool:
            raise TypeError("killed must be exact bool")
        if type(self.probe_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", self.probe_hash) is None:
            raise ValueError("probe_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class MutationReport:
    rows: tuple[MutationRow, ...]
    total: int
    killed: int
    survived: int
    passed: bool

    def __post_init__(self) -> None:
        if type(self.rows) is not tuple or any(type(row) is not MutationRow for row in self.rows):
            raise TypeError("rows must contain exact MutationRow values")
        expected = (
            len(self.rows),
            sum(row.killed for row in self.rows),
            sum(not row.killed for row in self.rows),
            all(row.killed for row in self.rows),
        )
        actual = (self.total, self.killed, self.survived, self.passed)
        if actual != expected:
            raise ValueError("mutation totals do not reconstruct from rows")

    def to_dict(self) -> dict[str, object]:
        return {
            "killed": self.killed,
            "passed": self.passed,
            "rows": [
                {"killed": row.killed, "name": row.name, "probe_hash": row.probe_hash}
                for row in self.rows
            ],
            "survived": self.survived,
            "total": self.total,
        }


MUTANTS: Final = (
    Mutant("id_inference", "tests.test_phase7_legacy_state", "_probe_id_inference"),
    Mutant("dual_write", "tests.test_phase7_sqlite_store", "_probe_dual_write"),
    Mutant("bool_as_int", "tests.test_phase7_types", "_probe_bool_as_int"),
    Mutant("stale_confirmation", "tests.test_phase7_dispatch", "_probe_stale_confirmation"),
    Mutant("command_in_turn", "tests.test_phase7_dispatch", "_probe_command_in_turn"),
    Mutant("alias_escalation", "tests.test_phase7_dispatch", "_probe_alias_escalation"),
    Mutant("deadline_write", "tests.test_phase7_coordinator", "_probe_deadline_write"),
    Mutant("cas_bypass", "tests.test_phase7_sqlite_store", "_probe_cas_bypass"),
    Mutant("comparator_downgrade", "tests.test_phase7_shadow", "_probe_comparator_downgrade"),
    Mutant("plugin_business_guard", "tests.test_phase7_runtime_patch", "_probe_plugin_business_guard"),
    Mutant("process_execution", "tests.test_phase7_purity", "_probe_process_execution"),
    Mutant("duplicate_json", "tests.test_phase7_serialization", "_probe_duplicate_json"),
)


def _state() -> BoundaryState:
    return BoundaryState(7, "lead-mutation-001", 0, None, None, (), ())


def _request(name: str, arguments, *, deadline=None) -> ToolDispatchRequest:
    return ToolDispatchRequest(
        name,
        arguments,
        "lead-mutation-001",
        "event-mutation-001",
        deadline or T0 + timedelta(minutes=1),
    )


def _probe_id_inference() -> bool:
    baseline = synthetic_collecting_snapshot()
    raw = {key: value for key, value in baseline.raw_fields.items()}
    raw["stage"] = "fechamento"
    raw["metadata"] = {
        "workflow_id": "workflow-synthetic-000000",
        "state_updated_at": T0.isoformat(),
        "room_name": "Public room label",
    }
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    candidate = LegacyLeadSnapshot(
        1,
        baseline.source,
        raw,
        canonical,
        hashlib.sha256(canonical.encode()).hexdigest(),
    )
    result = import_legacy_state(candidate)
    return result.disposition is ImportDisposition.MANUAL_REVIEW and result.state is None


def _probe_dual_write() -> bool:
    surface = set(dir(LegacyStateReadPort))
    return not surface.intersection({"write", "upsert", "delete"})


def _probe_bool_as_int() -> bool:
    try:
        IntegerSlot(True)
    except TypeError:
        return True
    return False


def _probe_stale_confirmation() -> bool:
    arguments = ActivityReservationArguments("offer-001", 1, "a" * 64)
    try:
        ToolDispatch().dispatch(
            _request("bokun_agendar_passeio_v2", arguments),
            current_state=_state(),
            now=T0,
        )
    except DispatchRejected:
        return True
    return False


def _probe_command_in_turn() -> bool:
    result = ToolDispatch().dispatch(
        _request(
            "cloudbeds_gerar_link_pagamento_stripe",
            StripeLinkArguments("anchor-001", DecimalSlot("125.00"), "BRL"),
        ),
        current_state=_state(),
        now=T0,
    )
    return result.reason is TurnPlanReason.MANUAL_REVIEW and result.commands == ()


def _probe_alias_escalation() -> bool:
    try:
        ToolDispatch().dispatch(
            _request(
                "availability",
                ActivityReservationArguments("offer-001", 1, "a" * 64),
            ),
            current_state=_state(),
            now=T0,
        )
    except DispatchRejected:
        return True
    return False


def _probe_deadline_write() -> bool:
    try:
        ToolDispatch().dispatch(
            _request(
                "cloudbeds_consultar_hospedagem_v2",
                LodgingReadArguments(T0.date(), (T0 + timedelta(days=1)).date(), 1),
                deadline=T0,
            ),
            current_state=_state(),
            now=T0,
        )
    except DispatchRejected:
        return True
    return False


def _probe_cas_bypass() -> bool:
    store = SQLiteBoundaryStore.open_memory()
    try:
        source = synthetic_collecting_snapshot()
        result = import_legacy_state(source)
        store.import_genesis(source, result, claimed_at=T0)
        current, stale = store.acquire_fence(source.raw_fields["lead_key"])
        store.acquire_fence(source.raw_fields["lead_key"])
        state = replace(current.state, version=1)
        try:
            store.commit(
                event_id="event-mutation-cas",
                event_hash="a" * 64,
                expected_version=0,
                fencing_token=stale,
                commit=BoundaryCommit(state, (), (), ()),
                committed_at=T0,
            )
        except ConcurrencyConflict:
            return True
        return False
    finally:
        store.close()


def _probe_comparator_downgrade() -> bool:
    old = DecisionObservation(
        False,
        "a" * 64,
        ("command-001",),
        ("command",),
        ("effect_confirmed",),
        ("b" * 64,),
        ("state", "event", "command", "outbox"),
        "reservation",
        "c" * 64,
        ("synthetic",),
    )
    return compare(old, replace(old, handoff_required=True)).severity is DivergenceSeverity.CRITICAL


def _probe_plugin_business_guard() -> bool:
    source = Path(__file__).with_name("dispatch.py")
    tree = ast.parse(source.read_text())
    forbidden_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        and any(token in node.name.casefold() for token in ("discount", "price_guard", "availability_guard"))
    }
    return not forbidden_names


def _probe_process_execution() -> bool:
    root = Path(__file__).parent
    forbidden = {"subprocess", "socket", "urllib", "requests", "httpx"}
    for source in root.glob("*.py"):
        if source.name in {"mutations.py", "faults.py"}:
            continue
        tree = ast.parse(source.read_text())
        modules = {
            alias.name.split(".")[0]
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        modules |= {
            node.module.split(".")[0]
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        if modules & forbidden:
            return False
    return True


def _probe_duplicate_json() -> bool:
    lease = TurnLease("lead-001", 1, T0 + timedelta(minutes=1))
    payload = to_wire_json(lease)
    duplicate = payload.replace('"schema_version":1', '"schema_version":1,"schema_version":1')
    try:
        from_wire_json(duplicate, TurnLease)
    except ValueError:
        return True
    return False


_PROBES: Final[dict[str, Callable[[], bool]]] = {
    name: value
    for name, value in globals().items()
    if name.startswith("_probe_") and callable(value)
}


def run_mutations(
    *,
    focused: bool,
    frozen_tree: str | None = None,
    current_tree: str | None = None,
) -> MutationReport:
    if type(focused) is not bool:
        raise TypeError("focused must be an exact bool")
    if not focused:
        assert_frozen_candidate(frozen_tree=frozen_tree, current_tree=current_tree)
    selected = MUTANTS[:6] if focused else MUTANTS
    rows = []
    for mutant in selected:
        probe = _PROBES.get(mutant.probe_name)
        killed = False if probe is None else probe()
        probe_hash = hashlib.sha256(
            f"{mutant.name}:{mutant.owner}:{mutant.probe_name}:{int(killed)}".encode()
        ).hexdigest()
        rows.append(MutationRow(mutant.name, killed, probe_hash))
    exact_rows = tuple(rows)
    return MutationReport(
        exact_rows,
        len(exact_rows),
        sum(row.killed for row in exact_rows),
        sum(not row.killed for row in exact_rows),
        all(row.killed for row in exact_rows),
    )


__all__ = (
    "MUTANTS",
    "Mutant",
    "MutationReport",
    "MutationRow",
    "run_mutations",
)
