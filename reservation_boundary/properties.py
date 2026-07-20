"""Deterministic capability-free Phase 7 property sequences."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
import re
from typing import Final

from reservation_domain import new_workflow

from reservation_boundary.dispatch import ToolDispatch
from reservation_boundary.legacy_state import import_legacy_state
from reservation_boundary.serialization import from_wire_json, semantic_hash, to_wire_json
from reservation_boundary.shadow import DecisionObservation, compare
from reservation_boundary.types import (
    BoundaryState,
    CommandMigrationDisposition,
    DecimalSlot,
    DivergenceSeverity,
    ImportDisposition,
    LegacyLeadSnapshot,
    StripeLinkArguments,
    ToolDispatchRequest,
    TurnPlanReason,
)


PROPERTY_SEED: Final = 2026072007
PROPERTY_CASES: Final = 20_000
_SCENARIOS: Final = ("wire", "importer", "blocked_dispatch", "shadow")
_TREE_RE = re.compile(r"^[0-9a-f]{40}$")
T0: Final = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def assert_frozen_candidate(
    *,
    frozen_tree: str | None,
    current_tree: str | None,
) -> str:
    if (
        type(frozen_tree) is not str
        or type(current_tree) is not str
        or _TREE_RE.fullmatch(frozen_tree) is None
        or current_tree != frozen_tree
    ):
        raise RuntimeError("integral mode requires matching PHASE7_FROZEN_TREE and git tree")
    return frozen_tree


def _row_hash(
    *,
    index: int,
    scenario: str,
    variant: int,
    passed: bool,
    observation_hash: str,
) -> str:
    material = json.dumps(
        {
            "index": index,
            "observation_hash": observation_hash,
            "passed": passed,
            "scenario": scenario,
            "variant": variant,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PropertyRow:
    index: int
    scenario: str
    variant: int
    passed: bool
    observation_hash: str
    row_hash: str

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise TypeError("index must be an exact nonnegative integer")
        if type(self.scenario) is not str or self.scenario not in _SCENARIOS:
            raise ValueError("scenario is outside the closed property catalog")
        if type(self.variant) is not int or self.variant < 0:
            raise TypeError("variant must be an exact nonnegative integer")
        if type(self.passed) is not bool:
            raise TypeError("passed must be an exact bool")
        for value, name in (
            (self.observation_hash, "observation_hash"),
            (self.row_hash, "row_hash"),
        ):
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256")
        expected = _row_hash(
            index=self.index,
            scenario=self.scenario,
            variant=self.variant,
            passed=self.passed,
            observation_hash=self.observation_hash,
        )
        if self.row_hash != expected:
            raise ValueError("row_hash does not bind the property row")


@dataclass(frozen=True, slots=True)
class PropertyReport:
    seed: int
    total: int
    passed: bool
    scenario_counts: tuple[tuple[str, int], ...]
    rows: tuple[PropertyRow, ...]

    def __post_init__(self) -> None:
        if type(self.seed) is not int or type(self.seed) is bool:
            raise TypeError("seed must be an exact integer")
        if type(self.total) is not int or self.total < 1:
            raise TypeError("total must be an exact positive integer")
        if type(self.passed) is not bool:
            raise TypeError("passed must be an exact bool")
        if type(self.rows) is not tuple or any(type(row) is not PropertyRow for row in self.rows):
            raise TypeError("rows must contain exact PropertyRow values")
        for row in self.rows:
            PropertyRow(
                row.index,
                row.scenario,
                row.variant,
                row.passed,
                row.observation_hash,
                row.row_hash,
            )
        if tuple(row.index for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("property row indexes must be contiguous")
        counts = tuple(
            (name, sum(row.scenario == name for row in self.rows))
            for name in _SCENARIOS
        )
        if self.total != len(self.rows) or self.scenario_counts != counts:
            raise ValueError("property totals do not reconstruct from rows")
        if self.passed != all(row.passed for row in self.rows):
            raise ValueError("report passed flag must derive from rows")

    @classmethod
    def from_rows(cls, seed: int, rows: tuple[PropertyRow, ...]) -> "PropertyReport":
        if type(rows) is not tuple:
            raise TypeError("rows must be an exact tuple")
        return cls(
            seed,
            len(rows),
            all(row.passed for row in rows),
            tuple((name, sum(row.scenario == name for row in rows)) for name in _SCENARIOS),
            rows,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "rows": [
                {
                    "index": row.index,
                    "observation_hash": row.observation_hash,
                    "passed": row.passed,
                    "row_hash": row.row_hash,
                    "scenario": row.scenario,
                    "variant": row.variant,
                }
                for row in self.rows
            ],
            "scenario_counts": dict(self.scenario_counts),
            "seed": self.seed,
            "total": self.total,
        }


def synthetic_collecting_snapshot(index: int = 0) -> LegacyLeadSnapshot:
    instant = T0 + timedelta(seconds=index)
    fields: dict[str, object] = {
        "phone": "+5500000000000",
        "subscriber_id": f"subscriber-synthetic-{index:06d}",
        "lead_key": f"lead-synthetic-{index:06d}",
        "language": "pt-BR" if index % 2 == 0 else "en",
        "is_foreign": bool(index % 2),
        "ai_status": "active",
        "stage": ("new", "hostel", "agencia")[index % 3],
        "desired_services": [],
        "missing_slots": [],
        "memory_long": "",
        "hostel_reservations": [],
        "agency_bookings": [],
        "metadata": {
            "workflow_id": f"workflow-synthetic-{index:06d}",
            "state_updated_at": instant.isoformat(),
        },
    }
    canonical = json.dumps(
        fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return LegacyLeadSnapshot(
        1,
        "chapada-leads-hermes",
        fields,
        canonical,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _observation(index: int) -> DecisionObservation:
    return DecisionObservation(
        False,
        "a" * 64,
        (f"command-{index:06d}",),
        ("command",),
        ("effect_confirmed",),
        (hashlib.sha256(f"evidence:{index}".encode()).hexdigest(),),
        ("state", "event", "command", "outbox"),
        "reservation",
        hashlib.sha256(f"copy:{index}".encode()).hexdigest(),
        ("synthetic",),
    )


def _run_case(seed: int, index: int) -> PropertyRow:
    rng = random.Random((seed << 32) ^ index)
    scenario = _SCENARIOS[index % len(_SCENARIOS)]
    variant = rng.randrange(1, 1_000_000)
    passed = False
    observation_hash = "0" * 64
    if scenario == "wire":
        state = BoundaryState(
            7,
            f"lead-wire-{index:06d}",
            0,
            new_workflow(
                workflow_id=f"workflow-wire-{index:06d}",
                started_at=T0 + timedelta(seconds=index),
            ),
            None,
            (),
            (),
        )
        wire = to_wire_json(state)
        passed = from_wire_json(wire, BoundaryState) == state
        observation_hash = semantic_hash(state)
    elif scenario == "importer":
        result = import_legacy_state(synthetic_collecting_snapshot(index))
        passed = (
            result.disposition is ImportDisposition.MIGRATED
            and result.state is not None
            and result.state.version == 0
        )
        observation_hash = semantic_hash(result)
    elif scenario == "blocked_dispatch":
        state = BoundaryState(7, f"lead-synthetic-{index:06d}", 0, None, None, (), ())
        request = ToolDispatchRequest(
            "cloudbeds_gerar_link_pagamento_stripe",
            StripeLinkArguments(
                f"anchor-{index:06d}",
                DecimalSlot(f"{1 + index % 999}.00"),
                "BRL",
            ),
            state.lead_key,
            f"event-{index:06d}",
            T0 + timedelta(minutes=1, seconds=index),
        )
        result = ToolDispatch().dispatch(request, current_state=state, now=T0)
        passed = (
            result.reason is TurnPlanReason.MANUAL_REVIEW
            and result.command_migration is CommandMigrationDisposition.BLOCKED_UNMIGRATED
            and result.commands == ()
        )
        observation_hash = hashlib.sha256(
            f"{result.tool_name}:{result.reason.value}:{variant}".encode()
        ).hexdigest()
    else:
        old = _observation(index)
        new = replace(
            old,
            diagnostic_tags=("synthetic", f"variant-{variant}"),
        )
        comparison = compare(old, new)
        passed = comparison.severity is DivergenceSeverity.NONCRITICAL
        observation_hash = comparison.new_hash
    row_hash = _row_hash(
        index=index,
        scenario=scenario,
        variant=variant,
        passed=passed,
        observation_hash=observation_hash,
    )
    return PropertyRow(index, scenario, variant, passed, observation_hash, row_hash)


def run_property_sequences(
    *,
    seed: int,
    cases: int,
    frozen_tree: str | None = None,
    current_tree: str | None = None,
) -> PropertyReport:
    if type(seed) is not int or type(seed) is bool:
        raise TypeError("seed must be an exact integer")
    if type(cases) is not int or cases < 1 or cases > 1_000_000:
        raise ValueError("cases must be an exact integer in 1..1000000")
    if cases >= PROPERTY_CASES:
        assert_frozen_candidate(frozen_tree=frozen_tree, current_tree=current_tree)
    rows = tuple(_run_case(seed, index) for index in range(cases))
    return PropertyReport.from_rows(seed, rows)


__all__ = (
    "PROPERTY_CASES",
    "PROPERTY_SEED",
    "PropertyReport",
    "PropertyRow",
    "assert_frozen_candidate",
    "run_property_sequences",
    "synthetic_collecting_snapshot",
)
