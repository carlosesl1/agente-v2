"""Deterministic cross-workflow properties for Phase 6 follow-up workflows.

The package runner is capability-free: it uses only in-memory reservation
transitions and local temporary SQLite files. Process sharding belongs to the
CLI in ``scripts/run_phase6_properties.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from types import MappingProxyType

from reservation_domain import (
    ExecutionCertainty,
    ReservationCommand,
    ServiceKind,
    dumps_outcome,
    reduce,
)
from reservation_execution.properties import _build_case as _build_reservation_case
from reservation_lookup import ProviderKind

from .handoff import HandoffReasonCode, HandoffRequested
from .payment import (
    FinancialConfirmationReceived,
    FinancialSummaryRecorded,
    PaymentEvidenceRecorded,
    PaymentEvidenceTrust,
    PaymentMethodSelected,
    PixProofStatus,
    PixVisualEvidence,
    SettlementOutcome,
    StripeEventType,
    VerifiedStripeEvent,
    VerifiedWiseCredit,
    financial_summary_hash,
    stripe_target_fingerprint,
    wise_target_fingerprint,
)
from .reconciliation import PaymentReconciler, SettlementRecoveryDisposition
from .sqlite_store import IdentityConflict, SQLiteFollowupUnitOfWork
from .types import (
    BusinessUnit,
    ConfirmedReservationAnchor,
    EffectRequirement,
    HandoffEffectPolicy,
    HandoffReceipt,
    PaymentEffectPolicy,
    PaymentMethod,
    PaymentReceipt,
    PaymentStatus,
    PaymentSubject,
    SettlementCertainty,
)
from .workers import (
    HandoffOutboxWorker,
    HandoffWorkerDisposition,
    PaymentOutboxWorker,
    PaymentOutboxWorkerDisposition,
    PaymentSettlementWorker,
    SettlementWorkerDisposition,
)

_PHASE = "phase-06-handoff-and-payments"
_BASE_TIME = datetime(2029, 1, 1, tzinfo=timezone.utc)
_LEASE_TTL = timedelta(seconds=30)

POSITIVE_COUNTERS = (
    "handoff_cases",
    "payment_cases",
    "email_disabled_cases",
    "method_switches",
    "economic_version_changes",
    "pix_cases",
    "wise_cases",
    "stripe_cases",
    "evidence_conflicts",
    "pre_fence_recoveries",
    "post_fence_manual_reviews",
    "required_effect_deliveries",
    "optional_effect_failures",
)
SAFETY_COUNTERS = (
    "reservation_commands_after_anchor",
    "handoff_email_failures_do_not_block_required",
    "second_settlement_commands",
    "second_dispatch_slots",
    "proof_reuses",
    "outbox_settlement_retries",
    "unknown_automatic_retries",
    "partial_transactions",
    "wrong_target_settlements",
)
SERVICE_KEYS = (ServiceKind.LODGING.value, ServiceKind.ACTIVITY.value)
BUSINESS_UNIT_KEYS = (BusinessUnit.HOSTEL.value, BusinessUnit.AGENCY.value)
PAYMENT_METHOD_KEYS = tuple(method.value for method in PaymentMethod)
FOLLOWUP_MODES = (
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
_MODE_POSITIVE_NONZERO = MappingProxyType(
    {
        "handoff_pre_email_disabled": {"handoff_cases": 1, "email_disabled_cases": 1},
        "payment_pix_method_selected": {"payment_cases": 1, "pix_cases": 1},
        "handoff_post_success": {
            "handoff_cases": 1,
            "email_disabled_cases": 1,
            "required_effect_deliveries": 1,
        },
        "payment_wise_method_switch": {
            "payment_cases": 1,
            "method_switches": 1,
            "wise_cases": 1,
        },
        "handoff_manual_review": {"handoff_cases": 1, "email_disabled_cases": 1},
        "payment_stripe_economic_change": {
            "payment_cases": 1,
            "economic_version_changes": 1,
            "stripe_cases": 1,
        },
        "handoff_optional_email_failure": {
            "handoff_cases": 1,
            "required_effect_deliveries": 1,
            "optional_effect_failures": 1,
        },
        "payment_pix_evidence_conflict": {
            "payment_cases": 1,
            "pix_cases": 1,
            "evidence_conflicts": 1,
        },
        "handoff_pre_email_disabled_replay": {
            "handoff_cases": 1,
            "email_disabled_cases": 1,
        },
        "payment_wise_pre_fence_recovery": {
            "payment_cases": 1,
            "wise_cases": 1,
            "pre_fence_recoveries": 1,
        },
        "handoff_post_success_replay": {
            "handoff_cases": 1,
            "email_disabled_cases": 1,
        },
        "payment_stripe_post_fence_manual_review": {
            "payment_cases": 1,
            "stripe_cases": 1,
            "post_fence_manual_reviews": 1,
            "required_effect_deliveries": 1,
        },
        "handoff_manual_review_replay": {
            "handoff_cases": 1,
            "email_disabled_cases": 1,
        },
        "payment_pix_optional_effect_failure": {
            "payment_cases": 1,
            "pix_cases": 1,
            "required_effect_deliveries": 2,
            "optional_effect_failures": 1,
        },
        "handoff_optional_email_failure_replay": {
            "handoff_cases": 1,
            "required_effect_deliveries": 1,
            "optional_effect_failures": 1,
        },
        "payment_wise_method_selected_repeat": {
            "payment_cases": 1,
            "wise_cases": 1,
        },
    }
)
_PAYMENT_DIMENSIONS = (
    (ServiceKind.LODGING.value, BusinessUnit.HOSTEL.value),
    (ServiceKind.ACTIVITY.value, BusinessUnit.AGENCY.value),
)


def _expected_positive(mode: str) -> Mapping[str, int]:
    expected = {name: 0 for name in POSITIVE_COUNTERS}
    expected.update(_MODE_POSITIVE_NONZERO[mode])
    return MappingProxyType(expected)


def _expected_payment_method(mode: str) -> str | None:
    for method in PAYMENT_METHOD_KEYS:
        if f"_{method}_" in mode:
            return method
    return None


def _exact_nonnegative(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative exact integer")
    return value


def _exact_positive(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive exact integer")
    return value


def _closed_counter_map(
    value: object,
    names: tuple[str, ...],
    field_name: str,
) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise ValueError(f"{field_name} must contain the exact closed counter catalog")
    clean: dict[str, int] = {}
    for name in names:
        clean[name] = _exact_nonnegative(value[name], f"{field_name}.{name}")
    return MappingProxyType(clean)


def _optional_closed(value: object, allowed: tuple[str, ...], field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or value not in allowed:
        raise ValueError(f"{field_name} must use the closed catalog or None")
    return value


def _require_digest(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")
    return value


def _require_id(value: object, field_name: str) -> str:
    if type(value) is not str or value != value.strip() or len(value) < 3:
        raise ValueError(f"{field_name} must be a canonical identifier")
    return value


@dataclass(frozen=True, slots=True)
class FollowupPropertyRow:
    index: int
    case_kind: str
    mode: str
    service: str | None
    business_unit: str | None
    payment_method: str | None
    reservation_path_confirmed: bool
    reservation_workflow_id: str | None
    reservation_command_id: str | None
    reservation_outcome_hash: str | None
    positive: Mapping[str, int]
    safety: Mapping[str, int]

    def __post_init__(self) -> None:
        _exact_nonnegative(self.index, "property_row.index")
        if type(self.case_kind) is not str or self.case_kind not in ("handoff", "payment"):
            raise ValueError("property_row.case_kind must be handoff or payment")
        if type(self.mode) is not str or self.mode not in FOLLOWUP_MODES:
            raise ValueError("property_row.mode must use the closed mode catalog")
        if not self.mode.startswith(self.case_kind + "_"):
            raise ValueError("property_row mode and case kind disagree")
        service = _optional_closed(self.service, SERVICE_KEYS, "property_row.service")
        unit = _optional_closed(
            self.business_unit,
            BUSINESS_UNIT_KEYS,
            "property_row.business_unit",
        )
        method = _optional_closed(
            self.payment_method,
            PAYMENT_METHOD_KEYS,
            "property_row.payment_method",
        )
        if type(self.reservation_path_confirmed) is not bool:
            raise ValueError("reservation_path_confirmed must be an exact bool")
        identifiers = (
            self.reservation_workflow_id,
            self.reservation_command_id,
            self.reservation_outcome_hash,
        )
        if self.reservation_path_confirmed:
            workflow_id = _require_id(identifiers[0], "property_row.reservation_workflow_id")
            command_id = _require_id(identifiers[1], "property_row.reservation_command_id")
            outcome_hash = _require_digest(
                identifiers[2],
                "property_row.reservation_outcome_hash",
            )
            object.__setattr__(self, "reservation_workflow_id", workflow_id)
            object.__setattr__(self, "reservation_command_id", command_id)
            object.__setattr__(self, "reservation_outcome_hash", outcome_hash)
        elif any(value is not None for value in identifiers):
            raise ValueError("unconfirmed property row cannot retain reservation identities")
        positive = _closed_counter_map(
            self.positive,
            POSITIVE_COUNTERS,
            "property_row.positive",
        )
        safety = _closed_counter_map(
            self.safety,
            SAFETY_COUNTERS,
            "property_row.safety",
        )
        expected_cases = (1, 0) if self.case_kind == "handoff" else (0, 1)
        if (positive["handoff_cases"], positive["payment_cases"]) != expected_cases:
            raise ValueError("each property row must contribute exactly one bilateral case")
        if self.case_kind == "payment":
            if not self.reservation_path_confirmed or service is None or unit is None or method is None:
                raise ValueError("payment row requires a real reservation path and closed dimensions")
        elif method is not None:
            raise ValueError("handoff row cannot claim a payment method")
        object.__setattr__(self, "service", service)
        object.__setattr__(self, "business_unit", unit)
        object.__setattr__(self, "payment_method", method)
        object.__setattr__(self, "positive", positive)
        object.__setattr__(self, "safety", safety)

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "case_kind": self.case_kind,
            "mode": self.mode,
            "service": self.service,
            "business_unit": self.business_unit,
            "payment_method": self.payment_method,
            "reservation_path_confirmed": self.reservation_path_confirmed,
            "reservation_workflow_id": self.reservation_workflow_id,
            "reservation_command_id": self.reservation_command_id,
            "reservation_outcome_hash": self.reservation_outcome_hash,
            "positive": dict(self.positive),
            "safety": dict(self.safety),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "FollowupPropertyRow":
        if not isinstance(payload, Mapping):
            raise ValueError("property row payload must be a mapping")
        return cls(**dict(payload))


@dataclass(frozen=True, slots=True)
class FollowupPropertyAudit:
    start: int
    cases: int
    quick_check: str
    foreign_key_violations: int
    deep_audits: int

    def __post_init__(self) -> None:
        _exact_nonnegative(self.start, "property_audit.start")
        _exact_positive(self.cases, "property_audit.cases")
        if type(self.quick_check) is not str or self.quick_check not in ("ok", "failed"):
            raise ValueError("property_audit.quick_check must use the closed result catalog")
        _exact_nonnegative(
            self.foreign_key_violations,
            "property_audit.foreign_key_violations",
        )
        _exact_nonnegative(self.deep_audits, "property_audit.deep_audits")

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start,
            "cases": self.cases,
            "quick_check": self.quick_check,
            "foreign_key_violations": self.foreign_key_violations,
            "deep_audits": self.deep_audits,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "FollowupPropertyAudit":
        if not isinstance(payload, Mapping):
            raise ValueError("property audit payload must be a mapping")
        return cls(**dict(payload))


@dataclass(frozen=True, slots=True)
class FollowupPropertyReport:
    start: int
    cases: int
    seed: int
    rows: tuple[FollowupPropertyRow, ...]
    audits: tuple[FollowupPropertyAudit, ...]
    violations: tuple[str, ...]

    def __post_init__(self) -> None:
        _exact_nonnegative(self.start, "property_report.start")
        _exact_positive(self.cases, "property_report.cases")
        if type(self.seed) is not int:
            raise ValueError("property_report.seed must be an exact integer")
        if type(self.rows) is not tuple or any(
            type(row) is not FollowupPropertyRow for row in self.rows
        ):
            raise ValueError("property_report.rows must be an exact tuple of rows")
        if len(self.rows) != self.cases:
            raise ValueError("property report row count must equal cases")
        expected_indexes = tuple(range(self.start, self.start + self.cases))
        if tuple(row.index for row in self.rows) != expected_indexes:
            raise ValueError("property report rows must cover ordered global indexes exactly")
        if type(self.audits) is not tuple or not self.audits or any(
            type(audit) is not FollowupPropertyAudit for audit in self.audits
        ):
            raise ValueError("property_report.audits must be a nonempty exact tuple")
        audit_indexes = tuple(
            index
            for audit in self.audits
            for index in range(audit.start, audit.start + audit.cases)
        )
        if audit_indexes != expected_indexes:
            raise ValueError("property audits must partition the requested range exactly")
        if type(self.violations) is not tuple or any(
            type(item) is not str or not item for item in self.violations
        ):
            raise ValueError("property_report.violations must be exact nonempty strings")

    def _counter(self, name: str, catalog: tuple[str, ...]) -> int:
        return sum(getattr(row, "positive" if catalog is POSITIVE_COUNTERS else "safety")[name] for row in self.rows)

    @property
    def handoff_cases(self) -> int:
        return self._counter("handoff_cases", POSITIVE_COUNTERS)

    @property
    def payment_cases(self) -> int:
        return self._counter("payment_cases", POSITIVE_COUNTERS)

    @property
    def email_disabled_cases(self) -> int:
        return self._counter("email_disabled_cases", POSITIVE_COUNTERS)

    @property
    def method_switches(self) -> int:
        return self._counter("method_switches", POSITIVE_COUNTERS)

    @property
    def economic_version_changes(self) -> int:
        return self._counter("economic_version_changes", POSITIVE_COUNTERS)

    @property
    def pix_cases(self) -> int:
        return self._counter("pix_cases", POSITIVE_COUNTERS)

    @property
    def wise_cases(self) -> int:
        return self._counter("wise_cases", POSITIVE_COUNTERS)

    @property
    def stripe_cases(self) -> int:
        return self._counter("stripe_cases", POSITIVE_COUNTERS)

    @property
    def evidence_conflicts(self) -> int:
        return self._counter("evidence_conflicts", POSITIVE_COUNTERS)

    @property
    def pre_fence_recoveries(self) -> int:
        return self._counter("pre_fence_recoveries", POSITIVE_COUNTERS)

    @property
    def post_fence_manual_reviews(self) -> int:
        return self._counter("post_fence_manual_reviews", POSITIVE_COUNTERS)

    @property
    def required_effect_deliveries(self) -> int:
        return self._counter("required_effect_deliveries", POSITIVE_COUNTERS)

    @property
    def optional_effect_failures(self) -> int:
        return self._counter("optional_effect_failures", POSITIVE_COUNTERS)

    @property
    def reservation_commands_after_anchor(self) -> int:
        return self._counter("reservation_commands_after_anchor", SAFETY_COUNTERS)

    @property
    def handoff_email_failures_do_not_block_required(self) -> int:
        return self._counter(
            "handoff_email_failures_do_not_block_required",
            SAFETY_COUNTERS,
        )

    @property
    def second_settlement_commands(self) -> int:
        return self._counter("second_settlement_commands", SAFETY_COUNTERS)

    @property
    def second_dispatch_slots(self) -> int:
        return self._counter("second_dispatch_slots", SAFETY_COUNTERS)

    @property
    def proof_reuses(self) -> int:
        return self._counter("proof_reuses", SAFETY_COUNTERS)

    @property
    def outbox_settlement_retries(self) -> int:
        return self._counter("outbox_settlement_retries", SAFETY_COUNTERS)

    @property
    def unknown_automatic_retries(self) -> int:
        return self._counter("unknown_automatic_retries", SAFETY_COUNTERS)

    @property
    def partial_transactions(self) -> int:
        return self._counter("partial_transactions", SAFETY_COUNTERS)

    @property
    def wrong_target_settlements(self) -> int:
        return self._counter("wrong_target_settlements", SAFETY_COUNTERS)

    @property
    def service_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            {key: sum(row.service == key for row in self.rows) for key in SERVICE_KEYS}
        )

    @property
    def business_unit_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            {
                key: sum(row.business_unit == key for row in self.rows)
                for key in BUSINESS_UNIT_KEYS
            }
        )

    @property
    def method_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            {
                key: sum(row.payment_method == key for row in self.rows)
                for key in PAYMENT_METHOD_KEYS
            }
        )

    @property
    def mode_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            {mode: sum(row.mode == mode for row in self.rows) for mode in FOLLOWUP_MODES}
        )

    @property
    def payment_dimension_counts(self) -> Mapping[str, int]:
        return MappingProxyType(
            {
                f"{method}|{service}|{unit}": sum(
                    row.case_kind == "payment"
                    and row.payment_method == method
                    and row.service == service
                    and row.business_unit == unit
                    for row in self.rows
                )
                for method in PAYMENT_METHOD_KEYS
                for service, unit in _PAYMENT_DIMENSIONS
            }
        )

    def _rows_follow_closed_contract(self) -> bool:
        return all(
            row.mode == FOLLOWUP_MODES[row.index % len(FOLLOWUP_MODES)]
            and row.positive == _expected_positive(row.mode)
            and row.payment_method == _expected_payment_method(row.mode)
            and (
                row.case_kind != "payment"
                or (row.service, row.business_unit) in _PAYMENT_DIMENSIONS
            )
            for row in self.rows
        )

    def _mode_cardinality_is_exact(self) -> bool:
        return all(
            self.mode_counts[mode]
            == sum(
                FOLLOWUP_MODES[index % len(FOLLOWUP_MODES)] == mode
                for index in range(self.start, self.start + self.cases)
            )
            for mode in FOLLOWUP_MODES
        )

    @property
    def passed(self) -> bool:
        return bool(
            self.cases >= len(FOLLOWUP_MODES)
            and self._rows_follow_closed_contract()
            and self._mode_cardinality_is_exact()
            and self.handoff_cases + self.payment_cases == self.cases
            and self.handoff_cases == self.payment_cases
            and all(getattr(self, name) > 0 for name in POSITIVE_COUNTERS)
            and all(getattr(self, name) == 0 for name in SAFETY_COUNTERS)
            and all(value > 0 for value in self.service_counts.values())
            and all(value > 0 for value in self.business_unit_counts.values())
            and all(value > 0 for value in self.method_counts.values())
            and all(value > 0 for value in self.mode_counts.values())
            and all(value > 0 for value in self.payment_dimension_counts.values())
            and all(audit.quick_check == "ok" for audit in self.audits)
            and all(audit.foreign_key_violations == 0 for audit in self.audits)
            and sum(audit.deep_audits for audit in self.audits) > 0
            and not self.violations
        )

    def to_dict(self) -> dict[str, object]:
        counters = {
            **{name: getattr(self, name) for name in POSITIVE_COUNTERS},
            **{name: getattr(self, name) for name in SAFETY_COUNTERS},
        }
        return {
            "start": self.start,
            "cases": self.cases,
            "seed": self.seed,
            "counters": counters,
            "service_counts": dict(self.service_counts),
            "business_unit_counts": dict(self.business_unit_counts),
            "method_counts": dict(self.method_counts),
            "mode_counts": dict(self.mode_counts),
            "payment_dimension_counts": dict(self.payment_dimension_counts),
            "rows": [row.to_dict() for row in self.rows],
            "audits": [audit.to_dict() for audit in self.audits],
            "violations": list(self.violations),
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "FollowupPropertyReport":
        if not isinstance(payload, Mapping):
            raise ValueError("property report payload must be a mapping")
        allowed = {
            "start",
            "cases",
            "seed",
            "counters",
            "service_counts",
            "business_unit_counts",
            "method_counts",
            "mode_counts",
            "payment_dimension_counts",
            "rows",
            "audits",
            "violations",
            "passed",
        }
        if set(payload) != allowed:
            raise ValueError("property report payload has a divergent schema")
        report = cls(
            start=payload["start"],
            cases=payload["cases"],
            seed=payload["seed"],
            rows=tuple(FollowupPropertyRow.from_dict(row) for row in payload["rows"]),
            audits=tuple(
                FollowupPropertyAudit.from_dict(audit) for audit in payload["audits"]
            ),
            violations=tuple(payload["violations"]),
        )
        if report.to_dict() != dict(payload):
            raise ValueError("property report payload counters do not reconstruct from rows")
        return report


def _zero_counters(names: tuple[str, ...]) -> dict[str, int]:
    return {name: 0 for name in names}


def _opaque_id(prefix: str, *parts: object) -> str:
    material = json.dumps(
        [prefix, *parts],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _confirmed_anchor(*, index: int, seed: int) -> ConfirmedReservationAnchor:
    provider = (
        ProviderKind.CLOUDBEDS
        if (index // 2 + seed) % 2 == 0
        else ProviderKind.BOKUN
    )
    script = _build_reservation_case(index=index, seed=seed, provider=provider)
    state = script.initial
    command = None
    for event, _ in script.events:
        transition = reduce(state, event)
        state = transition.state
        if transition.commands:
            if len(transition.commands) != 1 or command is not None:
                raise AssertionError("reservation property path emitted divergent commands")
            command = transition.commands[0]
    if type(command) is not ReservationCommand:
        raise AssertionError("reservation property path did not emit one real command")
    provider_reference = _opaque_id("provider-reference", seed, index)
    evidence = _digest({"reservation_effect": seed, "index": index})
    outcome = command.outcome(
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        normalized_status="synthetic_effect_confirmed",
        provider_reference=provider_reference,
        evidence=(evidence,),
    )
    outcome_hash = hashlib.sha256(dumps_outcome(outcome).encode("utf-8")).hexdigest()
    service = command.payload.components[0].service
    business_unit = (
        BusinessUnit.HOSTEL
        if provider is ProviderKind.CLOUDBEDS
        else BusinessUnit.AGENCY
    )
    confirmed_at = command.created_at + timedelta(seconds=1)
    return ConfirmedReservationAnchor(
        reservation_workflow_id=command.workflow_id,
        reservation_command_id=command.command_id,
        reservation_subject_signature=command.subject_signature,
        reservation_outcome_hash=outcome_hash,
        reservation_outcome=outcome,
        provider_reference=provider_reference,
        service=service,
        business_unit=business_unit,
        payment_target_id=_opaque_id("payment-target", seed, index),
        amount_minor=12_500 + (index % 1_000),
        currency="BRL",
        receiver_profile_id=_opaque_id("receiver-profile", business_unit.value),
        confirmed_at=confirmed_at,
        payment_deadline=confirmed_at + timedelta(days=2),
    )


def _trust(subject: PaymentSubject) -> PaymentEvidenceTrust:
    return PaymentEvidenceTrust(
        pix_receiver_profile_id=subject.receiver_profile_id,
        wise_signer_profile_id="wise-signer:property",
        wise_account_profile_id="wise-account:property",
        stripe_account_profile_id="stripe-account:property",
    )


def _pix_evidence(
    subject: PaymentSubject,
    *,
    identity: str,
    observed_at: datetime,
) -> PixVisualEvidence:
    tail = "ABCDEF" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:5].upper()
    values: dict[str, object] = {
        "proof_amount_minor": subject.amount_minor,
        "proof_currency": subject.currency,
        "proof_receiver_profile_id": subject.receiver_profile_id,
        "proof_status": PixProofStatus.PAID,
        "normalized_e2e": f"E1234567820290101{tail}",
        "observed_at": observed_at,
        "extractor_id": "extractor:phase6-property",
        "extractor_version": "extractor-version:phase6-property:1",
    }
    payload = {
        "type": "pix_visual_evidence",
        **{
            key: value.value
            if hasattr(value, "value")
            else value.isoformat()
            if hasattr(value, "isoformat")
            else value
            for key, value in values.items()
        },
    }
    return PixVisualEvidence(**values, evidence_hash=_digest(payload))


def _wise_evidence(
    subject: PaymentSubject,
    *,
    identity: str,
    observed_at: datetime,
) -> VerifiedWiseCredit:
    values: dict[str, object] = {
        "signer_profile_id": "wise-signer:property",
        "account_profile_id": "wise-account:property",
        "amount_minor": subject.amount_minor,
        "currency": subject.currency,
        "credited_at": observed_at,
        "transaction_fingerprint": _digest({"wise_transaction": identity}),
        "payer_fingerprint": _digest({"wise_payer": identity}),
        "reference_fingerprint": wise_target_fingerprint(subject.payment_target_id),
        "signature_verified": True,
    }
    payload = {
        "type": "verified_wise_credit",
        **{
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in values.items()
        },
    }
    return VerifiedWiseCredit(**values, verification_hash=_digest(payload))


def _stripe_evidence(
    subject: PaymentSubject,
    *,
    identity: str,
    observed_at: datetime,
) -> VerifiedStripeEvent:
    event_tail = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    values: dict[str, object] = {
        "stripe_account_profile_id": "stripe-account:property",
        "event_id": f"evt_{event_tail}",
        "payment_intent_fingerprint": stripe_target_fingerprint(
            subject.payment_target_id
        ),
        "amount_minor": subject.amount_minor,
        "currency": subject.currency,
        "event_type": StripeEventType.PAYMENT_INTENT_SUCCEEDED,
        "signature_verified": True,
        "observed_at": observed_at,
    }
    payload = {
        "type": "verified_stripe_event",
        **{
            key: value.value
            if hasattr(value, "value")
            else value.isoformat()
            if hasattr(value, "isoformat")
            else value
            for key, value in values.items()
        },
    }
    return VerifiedStripeEvent(**values, verification_hash=_digest(payload))


def _evidence_for(
    subject: PaymentSubject,
    method: PaymentMethod,
    *,
    identity: str,
    observed_at: datetime,
):
    if method is PaymentMethod.PIX:
        return _pix_evidence(subject, identity=identity, observed_at=observed_at)
    if method is PaymentMethod.WISE:
        return _wise_evidence(subject, identity=identity, observed_at=observed_at)
    if method is PaymentMethod.STRIPE:
        return _stripe_evidence(subject, identity=identity, observed_at=observed_at)
    raise AssertionError("closed payment method catalog diverged")


class _SettlementPort:
    settlement_id = "settlement-port:phase6-properties"
    settlement_version = 1

    def __init__(self) -> None:
        self.prepare_calls = 0
        self.dispatch_calls = 0

    def prepare(self, request):
        self.prepare_calls += 1
        return request.canonical_payload

    def dispatch(self, permit):
        self.dispatch_calls += 1
        return SettlementOutcome(
            certainty=SettlementCertainty.SETTLED,
            payment_registered=True,
            reservation_target_confirmed=True,
            provider_reference_fingerprint=_digest(
                {"settlement": permit.command.settlement_command_id}
            ),
            requires_reconciliation=False,
            claim_evidence=(permit.request_hash,),
        )


class _HandoffDelivery:
    delivery_id = "handoff-delivery:phase6-properties"
    delivery_version = 1

    def __init__(self, *, fail_optional_once: bool) -> None:
        self.fail_optional_once = fail_optional_once
        self.optional_failed = False
        self.last_required = False
        self.calls = 0
        self.now = _BASE_TIME

    def deliver(self, message):
        self.calls += 1
        self.last_required = message.required
        if not message.required and self.fail_optional_once and not self.optional_failed:
            self.optional_failed = True
            raise RuntimeError("synthetic optional handoff failure")
        return HandoffReceipt.for_message(
            message,
            receipt_id=_opaque_id("handoff-receipt", message.effect_id, self.calls),
            delivery_reference=_opaque_id(
                "handoff-delivery-reference",
                message.effect_id,
            ),
            delivery_id=self.delivery_id,
            delivery_version=self.delivery_version,
            delivered_at=self.now,
        )


class _PaymentDelivery:
    delivery_id = "payment-delivery:phase6-properties"
    delivery_version = 1

    def __init__(self, *, fail_optional_once: bool) -> None:
        self.fail_optional_once = fail_optional_once
        self.optional_failed = False
        self.last_required = False
        self.calls = 0
        self.now = _BASE_TIME

    def deliver(self, claim):
        self.calls += 1
        self.last_required = claim.message.required
        if not claim.message.required and self.fail_optional_once and not self.optional_failed:
            self.optional_failed = True
            raise RuntimeError("synthetic optional payment effect failure")
        return PaymentReceipt.for_claim(
            claim,
            receipt_id=_opaque_id("payment-receipt", claim.message_id, self.calls),
            delivery_reference=_opaque_id(
                "payment-delivery-reference",
                claim.message_id,
            ),
            delivered_at=self.now,
        )


def _row(
    *,
    index: int,
    case_kind: str,
    mode: str,
    anchor: ConfirmedReservationAnchor | None,
    method: PaymentMethod | None,
    positive: Mapping[str, int],
    safety: Mapping[str, int],
) -> FollowupPropertyRow:
    return FollowupPropertyRow(
        index=index,
        case_kind=case_kind,
        mode=mode,
        service=None if anchor is None else anchor.service.value,
        business_unit=None if anchor is None else anchor.business_unit.value,
        payment_method=None if method is None else method.value,
        reservation_path_confirmed=anchor is not None,
        reservation_workflow_id=(
            None if anchor is None else anchor.reservation_workflow_id
        ),
        reservation_command_id=(
            None if anchor is None else anchor.reservation_command_id
        ),
        reservation_outcome_hash=(
            None if anchor is None else anchor.reservation_outcome_hash
        ),
        positive=positive,
        safety=safety,
    )


def _drain_handoff_outbox(
    store: SQLiteFollowupUnitOfWork,
    *,
    index: int,
    now: datetime,
    fail_optional_once: bool,
    positive: dict[str, int],
    safety: dict[str, int],
) -> None:
    delivery = _HandoffDelivery(fail_optional_once=fail_optional_once)
    worker = HandoffOutboxWorker(
        store=store,
        delivery=delivery,
        worker_id=_opaque_id("handoff-worker", index),
        lease_ttl=_LEASE_TTL,
    )
    for offset in range(8):
        delivery.now = now + timedelta(microseconds=offset)
        result = worker.run_once(now=delivery.now)
        if result.disposition is HandoffWorkerDisposition.IDLE:
            return
        if result.disposition is HandoffWorkerDisposition.RETRYABLE_FAILURE:
            if delivery.last_required:
                safety["handoff_email_failures_do_not_block_required"] += 1
            else:
                positive["optional_effect_failures"] += 1
                if positive["required_effect_deliveries"] < 1:
                    safety["handoff_email_failures_do_not_block_required"] += 1
                return
        elif result.disposition is HandoffWorkerDisposition.DELIVERED:
            if delivery.last_required:
                positive["required_effect_deliveries"] += 1
            pending = store._connection.execute(
                "SELECT COUNT(*) FROM main.handoff_outbox WHERE status='pending'"
            ).fetchone()[0]
            if pending == 0:
                return
    raise AssertionError("handoff outbox did not become idle")


def _run_handoff_case(
    store: SQLiteFollowupUnitOfWork,
    *,
    index: int,
    seed: int,
    mode: str,
) -> FollowupPropertyRow:
    positive = _zero_counters(POSITIVE_COUNTERS)
    safety = _zero_counters(SAFETY_COUNTERS)
    positive["handoff_cases"] = 1
    optional_email = "optional_email_failure" in mode
    anchor = None
    if "post_success" in mode or "manual_review" in mode:
        anchor = _confirmed_anchor(index=index, seed=seed)
    if optional_email:
        policy = HandoffEffectPolicy(
            queue_state=EffectRequirement.REQUIRED,
            customer_acknowledgement=EffectRequirement.REQUIRED,
            internal_email=EffectRequirement.OPTIONAL,
        )
    else:
        policy = HandoffEffectPolicy.default_email_disabled()
        positive["email_disabled_cases"] = 1
    reason = (
        HandoffReasonCode.OPERATIONAL_REVIEW
        if "manual_review" in mode
        else HandoffReasonCode.CUSTOMER_REQUESTED
    )
    requested_at = (
        _BASE_TIME + timedelta(seconds=index * 20)
        if anchor is None
        else anchor.confirmed_at + timedelta(seconds=1)
    )
    request = HandoffRequested(
        handoff_id=_opaque_id("handoff", seed, index),
        lead_key_hash=_digest({"lead": seed, "index": index}),
        incident_key=_opaque_id("incident", seed, index),
        reason_code=reason,
        source_event_id=_opaque_id("handoff-source", seed, index),
        reservation_anchor=anchor,
        requested_at=requested_at,
    )
    opened = store.open_handoff(request, policy)
    if opened.state.request != request or not opened.state.queue_active:
        raise AssertionError("handoff did not open an active queue")
    if mode.endswith("_replay"):
        replay = store.open_handoff(request, policy)
        if replay.events or replay.effect_jobs:
            raise AssertionError("handoff identical replay was not a no-op")
    should_deliver = optional_email or mode == "handoff_post_success"
    if should_deliver:
        _drain_handoff_outbox(
            store,
            index=index,
            now=requested_at + timedelta(seconds=1),
            fail_optional_once=optional_email,
            positive=positive,
            safety=safety,
        )
    loaded = store.load_handoff(request.handoff_id)
    if optional_email and (
        loaded.acknowledgement is None or not loaded.queue_active
    ):
        safety["handoff_email_failures_do_not_block_required"] += 1
    return _row(
        index=index,
        case_kind="handoff",
        mode=mode,
        anchor=anchor,
        method=None,
        positive=positive,
        safety=safety,
    )


def _payment_policy(*, optional_effect: bool) -> PaymentEffectPolicy:
    return PaymentEffectPolicy(
        paid_state_transition=EffectRequirement.REQUIRED,
        customer_payment_confirmation=EffectRequirement.REQUIRED,
        internal_payment_email=(
            EffectRequirement.OPTIONAL
            if optional_effect
            else EffectRequirement.DISABLED
        ),
        booking_form=EffectRequirement.DISABLED,
    )


def _run_payment_prefix(
    store: SQLiteFollowupUnitOfWork,
    *,
    anchor: ConfirmedReservationAnchor,
    index: int,
    seed: int,
    method: PaymentMethod,
    method_switch: bool,
    economic_change: bool,
) -> int:
    opened = store.open_payment(anchor, _payment_policy(optional_effect=False))
    payment_id = opened.state.subject.payment_id
    revision = 0
    at = anchor.confirmed_at + timedelta(seconds=1)
    if method_switch:
        initial_method = PaymentMethod.WISE if method is PaymentMethod.PIX else PaymentMethod.PIX
        initial = store.apply_payment(
            payment_id,
            revision,
            PaymentMethodSelected(
                event_id=_opaque_id("payment-method-initial", seed, index),
                payment_id=payment_id,
                method=initial_method,
                selected_at=at,
            ),
        )
        revision += 1
        at += timedelta(seconds=1)
        if any(isinstance(command, ReservationCommand) for command in initial.commands):
            raise AssertionError("payment method selection emitted a reservation command")
    selected = store.apply_payment(
        payment_id,
        revision,
        PaymentMethodSelected(
            event_id=_opaque_id("payment-method", seed, index),
            payment_id=payment_id,
            method=method,
            selected_at=at,
        ),
    )
    revision += 1
    at += timedelta(seconds=1)
    latest = selected
    if economic_change:
        subject = PaymentSubject.from_anchor(
            anchor,
            payment_id=payment_id,
            method=method,
            amount_minor=anchor.amount_minor + 100,
            payment_version=2,
        )
        latest = store.apply_payment(
            payment_id,
            revision,
            FinancialSummaryRecorded(
                event_id=_opaque_id("payment-summary", seed, index),
                subject=subject,
                summary_hash=financial_summary_hash(subject),
                recorded_at=at,
            ),
        )
        revision += 1
    loaded = store.load_payment(payment_id)
    if loaded.subject.method is not method:
        raise AssertionError("payment prefix did not preserve the selected method")
    if economic_change and loaded.subject.payment_version != 2:
        raise AssertionError("economic change did not advance the payment version")
    if loaded.status is not PaymentStatus.AWAITING_FINANCIAL_CONFIRMATION:
        raise AssertionError("payment prefix left the expected pre-confirmation state")
    return sum(isinstance(command, ReservationCommand) for command in latest.commands)


def _prepare_payment(
    store: SQLiteFollowupUnitOfWork,
    *,
    anchor: ConfirmedReservationAnchor,
    index: int,
    seed: int,
    method: PaymentMethod,
    evidence_identity: str,
    method_switch: bool,
    economic_change: bool,
    optional_effect: bool,
):
    policy = _payment_policy(optional_effect=optional_effect)
    opened = store.open_payment(anchor, policy)
    payment_id = opened.state.subject.payment_id
    revision = 0
    at = anchor.confirmed_at + timedelta(seconds=1)
    if method_switch:
        initial_method = (
            PaymentMethod.WISE if method is PaymentMethod.PIX else PaymentMethod.PIX
        )
        selected = store.apply_payment(
            payment_id,
            revision,
            PaymentMethodSelected(
                event_id=_opaque_id("payment-method-initial", seed, index),
                payment_id=payment_id,
                method=initial_method,
                selected_at=at,
            ),
        )
        revision += 1
        at += timedelta(seconds=1)
        current = selected.state
    else:
        current = opened.state
    selected = store.apply_payment(
        payment_id,
        revision,
        PaymentMethodSelected(
            event_id=_opaque_id("payment-method", seed, index),
            payment_id=payment_id,
            method=method,
            selected_at=at,
        ),
    )
    revision += 1
    at += timedelta(seconds=1)
    subject = selected.state.subject
    if economic_change:
        subject = PaymentSubject.from_anchor(
            anchor,
            payment_id=payment_id,
            method=method,
            amount_minor=anchor.amount_minor + 100,
            payment_version=2,
        )
    summarized = store.apply_payment(
        payment_id,
        revision,
        FinancialSummaryRecorded(
            event_id=_opaque_id("payment-summary", seed, index),
            subject=subject,
            summary_hash=financial_summary_hash(subject),
            recorded_at=at,
        ),
    )
    revision += 1
    at += timedelta(seconds=1)
    confirmed = store.apply_payment(
        payment_id,
        revision,
        FinancialConfirmationReceived(
            event_id=_opaque_id("payment-confirmation-event", seed, index),
            payment_id=payment_id,
            payment_version=summarized.state.subject.payment_version,
            economic_signature=summarized.state.subject.economic_signature,
            summary_hash=summarized.state.summary.summary_hash,
            confirmation_id=_opaque_id("payment-confirmation", seed, index),
            confirmed_at=at,
        ),
    )
    revision += 1
    at += timedelta(seconds=1)
    evidence = _evidence_for(
        confirmed.state.subject,
        method,
        identity=evidence_identity,
        observed_at=at,
    )
    event = PaymentEvidenceRecorded(
        event_id=_opaque_id("payment-evidence", seed, index),
        payment_id=payment_id,
        payment_version=confirmed.state.subject.payment_version,
        economic_signature=confirmed.state.subject.economic_signature,
        evidence=evidence,
        trust=_trust(confirmed.state.subject),
        recorded_at=at,
    )
    return confirmed.state, event, revision, at


def _drain_payment_outbox(
    store: SQLiteFollowupUnitOfWork,
    *,
    index: int,
    now: datetime,
    fail_optional_once: bool,
    positive: dict[str, int],
) -> None:
    delivery = _PaymentDelivery(fail_optional_once=fail_optional_once)
    worker = PaymentOutboxWorker(
        store=store,
        delivery=delivery,
        worker_id=_opaque_id("payment-effect-worker", index),
        lease_ttl=_LEASE_TTL,
    )
    for offset in range(12):
        delivery.now = now + timedelta(microseconds=offset)
        result = worker.run_once(now=delivery.now)
        if result.disposition is PaymentOutboxWorkerDisposition.IDLE:
            return
        if result.disposition is PaymentOutboxWorkerDisposition.RETRYABLE_FAILURE:
            if not delivery.last_required:
                positive["optional_effect_failures"] += 1
                if positive["required_effect_deliveries"] < 2:
                    raise AssertionError("optional payment failure preceded required effects")
                return
        elif result.disposition is PaymentOutboxWorkerDisposition.DELIVERED:
            if delivery.last_required:
                positive["required_effect_deliveries"] += 1
            pending = store._connection.execute(
                "SELECT COUNT(*) FROM main.payment_outbox WHERE status='pending'"
            ).fetchone()[0]
            if pending == 0:
                return
    raise AssertionError("payment outbox did not become idle")


def _method_for_mode(mode: str) -> PaymentMethod:
    if "_pix_" in mode:
        return PaymentMethod.PIX
    if "_wise_" in mode:
        return PaymentMethod.WISE
    if "_stripe_" in mode:
        return PaymentMethod.STRIPE
    raise AssertionError("payment mode lacks a closed method")


def _record_payment_safety(
    store: SQLiteFollowupUnitOfWork,
    *,
    payment_id: str,
    claim_key: str,
    safety: dict[str, int],
):
    loaded = store.load_payment(payment_id)
    command_count = store._connection.execute(
        "SELECT COUNT(*) FROM main.payment_commands WHERE payment_id=?",
        (payment_id,),
    ).fetchone()[0]
    safety["second_settlement_commands"] += max(0, command_count - 1)
    slots = store._connection.execute(
        "SELECT dispatch_slots_consumed FROM main.payment_ledger WHERE payment_id=?",
        (payment_id,),
    ).fetchone()[0]
    safety["second_dispatch_slots"] += max(0, slots - 1)
    claim_reuses = store._connection.execute(
        "SELECT COUNT(DISTINCT payment_id) FROM main.payment_evidence_claims "
        "WHERE claim_key=?",
        (claim_key,),
    ).fetchone()[0]
    safety["proof_reuses"] += max(0, claim_reuses - 1)
    canonical_payload = json.loads(loaded.settlement_command.canonical_payload)
    if canonical_payload["payment_target_id"] != loaded.subject.payment_target_id:
        safety["wrong_target_settlements"] += 1
    return loaded


def _run_payment_case(
    store: SQLiteFollowupUnitOfWork,
    *,
    index: int,
    seed: int,
    mode: str,
) -> FollowupPropertyRow:
    positive = _zero_counters(POSITIVE_COUNTERS)
    safety = _zero_counters(SAFETY_COUNTERS)
    positive["payment_cases"] = 1
    method = _method_for_mode(mode)
    positive[f"{method.value}_cases"] = 1
    method_switch = "method_switch" in mode
    economic_change = "economic_change" in mode
    optional_effect = "optional_effect_failure" in mode
    if method_switch:
        positive["method_switches"] = 1
    if economic_change:
        positive["economic_version_changes"] = 1
    anchor = _confirmed_anchor(index=index, seed=seed)
    if any(
        marker in mode
        for marker in ("method_selected", "method_switch", "economic_change")
    ):
        safety["reservation_commands_after_anchor"] += _run_payment_prefix(
            store,
            anchor=anchor,
            index=index,
            seed=seed,
            method=method,
            method_switch=method_switch,
            economic_change=economic_change,
        )
        return _row(
            index=index,
            case_kind="payment",
            mode=mode,
            anchor=anchor,
            method=method,
            positive=positive,
            safety=safety,
        )
    evidence_identity = _opaque_id("proof-identity", seed, index)
    state, event, revision, event_at = _prepare_payment(
        store,
        anchor=anchor,
        index=index,
        seed=seed,
        method=method,
        evidence_identity=evidence_identity,
        method_switch=method_switch,
        economic_change=economic_change,
        optional_effect=optional_effect,
    )
    queued = store.claim_payment_evidence(
        state.subject.payment_id,
        revision,
        event,
    )
    safety["reservation_commands_after_anchor"] += sum(
        isinstance(command, ReservationCommand) for command in queued.commands
    )

    if "evidence_conflict" in mode:
        secondary_index = index + 100_000_000
        secondary_anchor = _confirmed_anchor(index=secondary_index, seed=seed)
        second_state, second_event, second_revision, _ = _prepare_payment(
            store,
            anchor=secondary_anchor,
            index=secondary_index,
            seed=seed,
            method=method,
            evidence_identity=evidence_identity,
            method_switch=False,
            economic_change=False,
            optional_effect=False,
        )
        try:
            store.claim_payment_evidence(
                second_state.subject.payment_id,
                second_revision,
                second_event,
            )
        except IdentityConflict:
            positive["evidence_conflicts"] += 1
        else:
            safety["proof_reuses"] += 1

    if any(
        marker in mode
        for marker in ("evidence_conflict", "queued_command")
    ):
        loaded = _record_payment_safety(
            store,
            payment_id=state.subject.payment_id,
            claim_key=queued.state.verified_evidence.claim_key,
            safety=safety,
        )
        if loaded.status is not PaymentStatus.SETTLEMENT_QUEUED:
            raise AssertionError("pre-settlement property mode left the queued state")
        return _row(
            index=index,
            case_kind="payment",
            mode=mode,
            anchor=anchor,
            method=method,
            positive=positive,
            safety=safety,
        )

    settlement_now = event_at + timedelta(seconds=1)
    settlement_port = _SettlementPort()
    post_fence = "post_fence_manual_review" in mode
    pre_fence = "pre_fence_recovery" in mode
    if pre_fence:
        claim = store.claim_settlement(
            worker_id=_opaque_id("expired-settlement-worker", seed, index),
            now=settlement_now,
            lease_ttl=_LEASE_TTL,
        )
        if claim is None:
            raise AssertionError("pre-fence recovery case did not claim a command")
        recovery = PaymentReconciler(store=store).run_once(
            now=claim.lease.expires_at
        )
        if recovery.disposition is SettlementRecoveryDisposition.PRE_FENCE_REQUEUED:
            positive["pre_fence_recoveries"] += 1
        else:
            raise AssertionError("pre-fence lease did not requeue at exact expiry")
        loaded = _record_payment_safety(
            store,
            payment_id=state.subject.payment_id,
            claim_key=queued.state.verified_evidence.claim_key,
            safety=safety,
        )
        if loaded.status is not PaymentStatus.SETTLEMENT_QUEUED:
            raise AssertionError("pre-fence recovery did not restore queued state")
        safety["unknown_automatic_retries"] += settlement_port.dispatch_calls
        return _row(
            index=index,
            case_kind="payment",
            mode=mode,
            anchor=anchor,
            method=method,
            positive=positive,
            safety=safety,
        )
    elif post_fence:
        claim = store.claim_settlement(
            worker_id=_opaque_id("fenced-settlement-worker", seed, index),
            now=settlement_now,
            lease_ttl=_LEASE_TTL,
        )
        if claim is None:
            raise AssertionError("post-fence recovery case did not claim a command")
        store.fence_settlement(
            claim,
            claim.command.canonical_payload,
            now=settlement_now,
        )
        recovery = PaymentReconciler(store=store).run_once(
            now=claim.lease.expires_at
        )
        if recovery.disposition is SettlementRecoveryDisposition.POST_FENCE_MANUAL_REVIEW:
            positive["post_fence_manual_reviews"] += 1
        else:
            raise AssertionError("post-fence lease did not become manual review")
        idle = PaymentSettlementWorker(
            store=store,
            settlement=settlement_port,
            worker_id=_opaque_id("forbidden-redispatch-worker", seed, index),
            lease_ttl=_LEASE_TTL,
        ).run_once(now=claim.lease.expires_at + timedelta(seconds=1))
        if idle.disposition is not SettlementWorkerDisposition.IDLE:
            safety["unknown_automatic_retries"] += 1
        safety["unknown_automatic_retries"] += settlement_port.dispatch_calls
        effects_now = claim.lease.expires_at + timedelta(seconds=2)
    else:
        result = PaymentSettlementWorker(
            store=store,
            settlement=settlement_port,
            worker_id=_opaque_id("settlement-worker", seed, index),
            lease_ttl=_LEASE_TTL,
        ).run_once(now=settlement_now)
        if result.disposition is not SettlementWorkerDisposition.SETTLED:
            raise AssertionError("settlement property case did not finish settled")
        effects_now = settlement_now + timedelta(seconds=1)

    dispatches_before_outbox = settlement_port.dispatch_calls
    _drain_payment_outbox(
        store,
        index=index,
        now=effects_now,
        fail_optional_once=optional_effect,
        positive=positive,
    )
    safety["outbox_settlement_retries"] += max(
        0,
        settlement_port.dispatch_calls - dispatches_before_outbox,
    )
    loaded = _record_payment_safety(
        store,
        payment_id=state.subject.payment_id,
        claim_key=queued.state.verified_evidence.claim_key,
        safety=safety,
    )
    expected_status = PaymentStatus.MANUAL_REVIEW if post_fence else PaymentStatus.PAID
    if loaded.status is not expected_status:
        raise AssertionError("payment property case reached a divergent terminal state")
    return _row(
        index=index,
        case_kind="payment",
        mode=mode,
        anchor=anchor,
        method=method,
        positive=positive,
        safety=safety,
    )


def _failure_row(*, index: int, mode: str) -> FollowupPropertyRow:
    positive = _zero_counters(POSITIVE_COUNTERS)
    case_kind = "handoff" if mode.startswith("handoff_") else "payment"
    positive[f"{case_kind}_cases"] = 1
    if case_kind == "payment":
        method = _method_for_mode(mode)
        positive[f"{method.value}_cases"] = 1
        anchor = _confirmed_anchor(index=index, seed=0)
    else:
        method = None
        anchor = None
    return _row(
        index=index,
        case_kind=case_kind,
        mode=mode,
        anchor=anchor,
        method=method,
        positive=positive,
        safety=_zero_counters(SAFETY_COUNTERS),
    )


def _run_followup_property_range(
    *,
    start: int,
    cases: int,
    seed: int,
    deep_consistency: bool = True,
) -> FollowupPropertyReport:
    _exact_nonnegative(start, "property_range.start")
    _exact_positive(cases, "property_range.cases")
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if type(deep_consistency) is not bool:
        raise TypeError("deep_consistency must be an exact bool")
    rows: list[FollowupPropertyRow] = []
    violations: list[str] = []
    deep_audits = 0
    quick_check = "ok"
    foreign_key_violations = 0
    template_store = SQLiteFollowupUnitOfWork.open(":memory:")
    try:
        for index in range(start, start + cases):
            mode = FOLLOWUP_MODES[index % len(FOLLOWUP_MODES)]
            connection = sqlite3.connect(":memory:", isolation_level=None, timeout=5.0)
            template_store._connection.backup(connection)
            store = SQLiteFollowupUnitOfWork.open(connection)
            store._connection.execute("PRAGMA journal_mode=MEMORY")
            store._connection.execute("PRAGMA synchronous=OFF")
            store._connection.execute("PRAGMA temp_store=MEMORY")
            try:
                if mode.startswith("handoff_"):
                    row = _run_handoff_case(
                        store,
                        index=index,
                        seed=seed,
                        mode=mode,
                    )
                else:
                    row = _run_payment_case(
                        store,
                        index=index,
                        seed=seed,
                        mode=mode,
                    )
                if index == start + cases - 1:
                    case_quick = store._connection.execute("PRAGMA quick_check").fetchone()[0]
                    case_foreign_keys = len(
                        store._connection.execute("PRAGMA foreign_key_check").fetchall()
                    )
                    foreign_key_violations += case_foreign_keys
                    if case_quick != "ok" or case_foreign_keys:
                        quick_check = "failed"
                        safety = dict(row.safety)
                        safety["partial_transactions"] += 1
                        row = replace(row, safety=MappingProxyType(safety))
                        if len(violations) < 64:
                            violations.append(
                                f"case={index} sqlite_structural_consistency_failed"
                            )
                if deep_consistency and index % len(FOLLOWUP_MODES) == 0:
                    if mode.startswith("handoff_"):
                        store.load_handoff(_opaque_id("handoff", seed, index))
                        deep_audits += 1
                    else:
                        payment_ids = tuple(
                            payment_id
                            for (payment_id,) in store._connection.execute(
                                "SELECT payment_id FROM main.payment_workflows ORDER BY payment_id"
                            )
                        )
                        for payment_id in payment_ids:
                            store.load_payment(payment_id)
                            deep_audits += 1
                rows.append(row)
            except Exception as exc:
                if len(violations) < 64:
                    violations.append(
                        f"case={index} mode={mode} unexpected={type(exc).__name__}:{exc}"
                    )
                rows.append(_failure_row(index=index, mode=mode))
                if index == start + cases - 1:
                    try:
                        case_quick = store._connection.execute(
                            "PRAGMA quick_check"
                        ).fetchone()[0]
                        case_foreign_keys = len(
                            store._connection.execute(
                                "PRAGMA foreign_key_check"
                            ).fetchall()
                        )
                    except Exception:
                        case_quick = "failed"
                        case_foreign_keys = 1
                    quick_check = "ok" if case_quick == "ok" else "failed"
                    foreign_key_violations += case_foreign_keys
            finally:
                store.close()
    finally:
        template_store.close()
    audit = FollowupPropertyAudit(
        start=start,
        cases=cases,
        quick_check=quick_check,
        foreign_key_violations=foreign_key_violations,
        deep_audits=deep_audits,
    )
    return FollowupPropertyReport(
        start=start,
        cases=cases,
        seed=seed,
        rows=tuple(rows),
        audits=(audit,),
        violations=tuple(violations),
    )


def _merge_followup_property_reports(
    reports: tuple[FollowupPropertyReport, ...],
    *,
    cases: int,
    seed: int,
) -> FollowupPropertyReport:
    if type(reports) is not tuple or not reports or any(
        type(report) is not FollowupPropertyReport for report in reports
    ):
        raise ValueError("reports must be a nonempty exact tuple")
    _exact_positive(cases, "merged_property_report.cases")
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if any(report.seed != seed for report in reports):
        raise ValueError("all property report seeds must match")
    ordered = tuple(sorted(reports, key=lambda report: report.start))
    if ordered[0].start != 0 or sum(report.cases for report in ordered) != cases:
        raise ValueError("property report shards do not match the requested total")
    return FollowupPropertyReport(
        start=0,
        cases=cases,
        seed=seed,
        rows=tuple(row for report in ordered for row in report.rows),
        audits=tuple(audit for report in ordered for audit in report.audits),
        violations=tuple(
            violation for report in ordered for violation in report.violations
        ),
    )


def run_followup_properties(*, cases: int, seed: int) -> FollowupPropertyReport:
    return _run_followup_property_range(
        start=0,
        cases=cases,
        seed=seed,
        deep_consistency=True,
    )


__all__ = [
    "POSITIVE_COUNTERS",
    "SAFETY_COUNTERS",
    "SERVICE_KEYS",
    "BUSINESS_UNIT_KEYS",
    "PAYMENT_METHOD_KEYS",
    "FOLLOWUP_MODES",
    "FollowupPropertyRow",
    "FollowupPropertyAudit",
    "FollowupPropertyReport",
    "run_followup_properties",
]
