"""Independent deterministic comparison of normalized old/new decisions."""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
import re
from typing import Final

from reservation_boundary.types import DivergenceSeverity


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_DISPATCH_KINDS: Final = frozenset(("read", "command", "state_commit"))
_EFFECT_CERTAINTIES: Final = frozenset(
    (
        "not_called",
        "called_no_effect",
        "effect_confirmed",
        "called_unknown",
        "not_dispatched",
        "dispatched_no_effect",
        "settled",
        "partial_settlement",
        "dispatched_unknown",
    )
)
_PERSISTENCE_STEPS: Final = frozenset(("state", "event", "command", "outbox"))
CRITICAL_FIELDS: Final = frozenset(
    (
        "handoff_required",
        "subject_signature",
        "command_identities",
        "dispatch_kinds",
        "effect_certainties",
        "claim_evidence",
        "persistence_order",
    )
)
NONCRITICAL_FIELDS: Final = frozenset(
    ("route_label", "copy_hash", "diagnostic_tags")
)


def _hash(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _id(value: object, name: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an exact opaque identifier")
    return value


def _exact_tuple(value: object, name: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an exact tuple")
    return value


@dataclass(frozen=True, slots=True)
class DecisionObservation:
    handoff_required: bool
    subject_signature: str | None
    command_identities: tuple[str, ...]
    dispatch_kinds: tuple[str, ...]
    effect_certainties: tuple[str, ...]
    claim_evidence: tuple[str, ...]
    persistence_order: tuple[str, ...]
    route_label: str
    copy_hash: str
    diagnostic_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.handoff_required) is not bool:
            raise TypeError("handoff_required must be an exact bool")
        _hash(self.subject_signature, "subject_signature", optional=True)
        command_ids = _exact_tuple(self.command_identities, "command_identities")
        if any(type(item) is not str for item in command_ids):
            raise TypeError("command identities must be exact strings")
        for item in command_ids:
            _id(item, "command identity")
        if len(set(command_ids)) != len(command_ids):
            raise ValueError("command identities must be unique")

        dispatch = _exact_tuple(self.dispatch_kinds, "dispatch_kinds")
        if any(type(item) is not str or item not in _DISPATCH_KINDS for item in dispatch):
            raise ValueError("dispatch kinds are outside the closed set")

        certainties = _exact_tuple(self.effect_certainties, "effect_certainties")
        if any(
            type(item) is not str or item not in _EFFECT_CERTAINTIES
            for item in certainties
        ):
            raise ValueError("effect certainties are outside the closed set")

        evidence = _exact_tuple(self.claim_evidence, "claim_evidence")
        for item in evidence:
            _hash(item, "claim evidence")
        if len(set(evidence)) != len(evidence):
            raise ValueError("claim evidence must be unique")

        order = _exact_tuple(self.persistence_order, "persistence_order")
        if any(type(item) is not str or item not in _PERSISTENCE_STEPS for item in order):
            raise ValueError("persistence order is outside the closed set")
        if len(set(order)) != len(order):
            raise ValueError("persistence order steps must be unique")

        _id(self.route_label, "route_label")
        _hash(self.copy_hash, "copy_hash")
        tags = _exact_tuple(self.diagnostic_tags, "diagnostic_tags")
        for item in tags:
            _id(item, "diagnostic tag")
        if len(set(tags)) != len(tags):
            raise ValueError("diagnostic tags must be unique")


@dataclass(frozen=True, slots=True)
class DecisionComparison:
    old_hash: str
    new_hash: str
    changed_fields: tuple[str, ...]
    severity: DivergenceSeverity

    def __post_init__(self) -> None:
        _hash(self.old_hash, "old_hash")
        _hash(self.new_hash, "new_hash")
        changed = _exact_tuple(self.changed_fields, "changed_fields")
        if (
            any(type(item) is not str for item in changed)
            or tuple(sorted(changed)) != changed
            or len(set(changed)) != len(changed)
            or not set(changed) <= CRITICAL_FIELDS | NONCRITICAL_FIELDS
        ):
            raise ValueError("changed_fields must be sorted unique closed names")
        if type(self.severity) is not DivergenceSeverity:
            raise TypeError("severity must be exact DivergenceSeverity")
        expected = _severity(frozenset(changed))
        if self.severity is not expected:
            raise ValueError("severity does not derive from changed_fields")
        if not changed and self.old_hash != self.new_hash:
            raise ValueError("equivalent observations must share semantic hash")


@dataclass(frozen=True, slots=True)
class DecisionComparisonSummary:
    rows: tuple[DecisionComparison, ...]
    total: int
    equivalent: int
    noncritical: int
    critical: int

    def __post_init__(self) -> None:
        rows = _exact_tuple(self.rows, "rows")
        if any(type(item) is not DecisionComparison for item in rows):
            raise TypeError("rows must contain exact DecisionComparison values")
        counts = {
            DivergenceSeverity.EQUIVALENT: sum(
                row.severity is DivergenceSeverity.EQUIVALENT for row in rows
            ),
            DivergenceSeverity.NONCRITICAL: sum(
                row.severity is DivergenceSeverity.NONCRITICAL for row in rows
            ),
            DivergenceSeverity.CRITICAL: sum(
                row.severity is DivergenceSeverity.CRITICAL for row in rows
            ),
        }
        values = (self.total, self.equivalent, self.noncritical, self.critical)
        if any(type(item) is not int or item < 0 for item in values):
            raise TypeError("summary counts must be exact nonnegative integers")
        expected = (
            len(rows),
            counts[DivergenceSeverity.EQUIVALENT],
            counts[DivergenceSeverity.NONCRITICAL],
            counts[DivergenceSeverity.CRITICAL],
        )
        if values != expected:
            raise ValueError("summary totals do not reconstruct from rows")

    @classmethod
    def from_rows(
        cls,
        rows: tuple[DecisionComparison, ...],
    ) -> "DecisionComparisonSummary":
        if type(rows) is not tuple:
            raise TypeError("rows must be an exact tuple")
        return cls(
            rows,
            len(rows),
            sum(row.severity is DivergenceSeverity.EQUIVALENT for row in rows),
            sum(row.severity is DivergenceSeverity.NONCRITICAL for row in rows),
            sum(row.severity is DivergenceSeverity.CRITICAL for row in rows),
        )


def _validated(value: object) -> DecisionObservation:
    if type(value) is not DecisionObservation:
        raise TypeError("observation must be exact DecisionObservation")
    reconstructed = DecisionObservation(
        **{field.name: getattr(value, field.name) for field in fields(DecisionObservation)}
    )
    if reconstructed != value:
        raise ValueError("observation is noncanonical")
    return value


def _semantic_hash(value: DecisionObservation) -> str:
    payload = {
        field.name: (
            list(item) if type(item) is tuple else item
        )
        for field in fields(DecisionObservation)
        for item in (getattr(value, field.name),)
    }
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _severity(changed: frozenset[str]) -> DivergenceSeverity:
    if not changed:
        return DivergenceSeverity.EQUIVALENT
    if changed & CRITICAL_FIELDS:
        return DivergenceSeverity.CRITICAL
    return DivergenceSeverity.NONCRITICAL


def compare(old: DecisionObservation, new: DecisionObservation) -> DecisionComparison:
    """Compare two independently normalized observations under literal policy."""

    exact_old = _validated(old)
    exact_new = _validated(new)
    changed = frozenset(
        field.name
        for field in fields(DecisionObservation)
        if getattr(exact_old, field.name) != getattr(exact_new, field.name)
    )
    return DecisionComparison(
        _semantic_hash(exact_old),
        _semantic_hash(exact_new),
        tuple(sorted(changed)),
        _severity(changed),
    )


__all__ = (
    "CRITICAL_FIELDS",
    "NONCRITICAL_FIELDS",
    "DecisionComparison",
    "DecisionComparisonSummary",
    "DecisionObservation",
    "compare",
)
