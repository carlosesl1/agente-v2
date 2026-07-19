"""Deterministic cross-phase operational properties for durable execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType

from reservation_confirmation import (
    ReferenceConfirmationClassifier,
    SummaryLocale,
    classify_and_bind,
    prepare_summary,
)
from reservation_domain import (
    CustomerFacts,
    DraftRequested,
    EconomicTerms,
    ExecutionCertainty,
    LookupRecorded,
    OfferChosen,
    ReadyToSummarizeState,
    StartSearch,
    dumps_command,
    loads_outcome,
    new_workflow,
    reduce,
)
from reservation_lookup import ProviderKind
from reservation_lookup.properties import _adapter_result

from .adapter import PreparationFailure
from .outbox import OutboxWorker, OutboxWorkerDisposition
from .projection import summary_outbox_message
from .reconciliation import Reconciler
from .sqlite_store import IdentityConflict, SQLiteUnitOfWork, StaleLease, StoreError
from .types import DeliveryReceipt, DispatchRequest, LedgerStatus, OutboxKind
from .worker import CommandWorker, WorkerDisposition

_PHASE = "phase-05-durable-command-execution"
_BASE_TIME = datetime(2028, 1, 1, tzinfo=timezone.utc)
_LEASE_TTL = timedelta(seconds=30)
_OUTCOME_KEYS = (
    "called_no_effect",
    "called_unknown",
    "effect_confirmed",
    "not_called",
)
_POSITIVE_FIELDS = (
    "authorized_commands",
    "terminal_commands",
    "summary_outboxes",
    "final_outboxes",
    "expired_lease_recoveries",
    "stale_token_rejections",
    "post_fence_unknowns",
    "manual_reviews",
    "delivery_retries",
    "duplicate_probes",
    "conflict_probes",
    "recovered_command_matches",
    "delivery_target_matches",
    "consistency_probes",
)
_SAFETY_FIELDS = (
    "unauthorized_commands",
    "second_commands",
    "second_dispatch_slots",
    "second_provider_calls",
    "unknown_redispatches",
    "outbox_provider_retries",
    "partial_transactions",
    "stale_token_writes",
    "missing_terminals",
    "unexpected_exceptions",
    "wrong_command_claims",
    "wrong_delivery_targets",
)


@dataclass(frozen=True, slots=True)
class Phase5PropertyReport:
    cases: int
    seed: int
    cloudbeds_cases: int
    bokun_cases: int
    outcome_counts: Mapping[str, int]
    authorized_commands: int
    terminal_commands: int
    summary_outboxes: int
    final_outboxes: int
    expired_lease_recoveries: int
    stale_token_rejections: int
    post_fence_unknowns: int
    manual_reviews: int
    delivery_retries: int
    duplicate_probes: int
    conflict_probes: int
    recovered_command_matches: int
    delivery_target_matches: int
    consistency_probes: int
    unauthorized_commands: int
    second_commands: int
    second_dispatch_slots: int
    second_provider_calls: int
    unknown_redispatches: int
    outbox_provider_retries: int
    partial_transactions: int
    stale_token_writes: int
    missing_terminals: int
    unexpected_exceptions: int
    wrong_command_claims: int
    wrong_delivery_targets: int
    violations: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "cases",
            "seed",
            "cloudbeds_cases",
            "bokun_cases",
            *_POSITIVE_FIELDS,
            *_SAFETY_FIELDS,
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be a non-negative exact integer")
        if not isinstance(self.outcome_counts, Mapping) or any(
            type(key) is not str or type(value) is not int or value < 0
            for key, value in self.outcome_counts.items()
        ):
            raise ValueError("outcome_counts must map strings to non-negative integers")
        object.__setattr__(
            self,
            "outcome_counts",
            MappingProxyType(dict(sorted(self.outcome_counts.items()))),
        )
        if type(self.violations) is not tuple or any(
            type(item) is not str for item in self.violations
        ):
            raise ValueError("violations must be an exact tuple of strings")

    @property
    def passed(self) -> bool:
        return bool(
            self.cases >= 8
            and self.cloudbeds_cases + self.bokun_cases == self.cases
            and self.cloudbeds_cases > 0
            and self.bokun_cases > 0
            and set(self.outcome_counts) == set(_OUTCOME_KEYS)
            and sum(self.outcome_counts.values()) == self.cases
            and all(value > 0 for value in self.outcome_counts.values())
            and self.authorized_commands == self.cases
            and self.terminal_commands == self.cases
            and self.summary_outboxes == self.cases
            and self.final_outboxes == self.cases
            and all(getattr(self, field_name) > 0 for field_name in _POSITIVE_FIELDS)
            and all(getattr(self, field_name) == 0 for field_name in _SAFETY_FIELDS)
            and not self.violations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "cases": self.cases,
            "seed": self.seed,
            "cloudbeds_cases": self.cloudbeds_cases,
            "bokun_cases": self.bokun_cases,
            "outcome_counts": dict(self.outcome_counts),
            **{field_name: getattr(self, field_name) for field_name in _POSITIVE_FIELDS},
            **{field_name: getattr(self, field_name) for field_name in _SAFETY_FIELDS},
            "violations": list(self.violations),
            "passed": self.passed,
        }


class _ScriptedExecutionAdapter:
    adapter_id = "phase5-property-adapter"
    adapter_version = 1

    def __init__(self, actions: tuple[object, ...]):
        self._actions = list(actions)
        self._command = None
        self.prepare_calls = 0
        self.dispatch_calls = 0

    def prepare(self, command):
        self.prepare_calls += 1
        self._command = command
        if self._actions and type(self._actions[0]) is PreparationFailure:
            raise self._actions.pop(0)
        return DispatchRequest.from_command(command, dumps_command(command))

    def dispatch(self, request, *, idempotency_key):
        self.dispatch_calls += 1
        if not self._actions:
            raise AssertionError("property adapter action underflow")
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        if type(action) is not ExecutionCertainty or self._command is None:
            raise AssertionError("property action must be an execution certainty")
        return self._command.outcome(
            certainty=action,
            normalized_status={
                ExecutionCertainty.EFFECT_CONFIRMED: "synthetic_effect_confirmed",
                ExecutionCertainty.CALLED_NO_EFFECT: "synthetic_no_effect",
                ExecutionCertainty.CALLED_UNKNOWN: "synthetic_unknown",
                ExecutionCertainty.NOT_CALLED: "synthetic_not_called",
            }[action],
            provider_reference=(
                None
                if action is ExecutionCertainty.NOT_CALLED
                else "provider:property-synthetic"
            ),
            evidence=(request.payload_hash,),
        )

    @property
    def command_id(self) -> str | None:
        return None if self._command is None else self._command.command_id


class _ScriptedDelivery:
    delivery_id = "phase5-property-delivery"
    delivery_version = 1

    def __init__(self, *, target_message_id: str):
        self._target_message_id = target_message_id
        self._now: datetime | None = None
        self.calls = 0
        self.target_calls = 0

    def set_now(self, now: datetime) -> None:
        self._now = now

    def deliver(self, message):
        self.calls += 1
        if message.message_id == self._target_message_id:
            self.target_calls += 1
        if message.message_id == self._target_message_id and self.target_calls == 1:
            raise RuntimeError("synthetic delivery failure")
        if self._now is None:
            raise AssertionError("delivery clock was not set")
        material = json.dumps(
            {
                "delivered_at": self._now.isoformat(),
                "delivery_reference": "delivery:property-synthetic",
                "message_id": message.message_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return DeliveryReceipt(
            message_id=message.message_id,
            delivery_reference="delivery:property-synthetic",
            receipt_hash=hashlib.sha256(material.encode("utf-8")).hexdigest(),
            delivered_at=self._now,
        )


@dataclass(frozen=True, slots=True)
class _CaseScript:
    initial: object
    events: tuple[tuple[object, tuple[object, ...]], ...]
    confirmation_revision: int
    confirmation_event: object


def _opaque_id(prefix: str, *parts: object) -> str:
    material = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(material).hexdigest()}"


def _build_case(*, index: int, seed: int, provider: ProviderKind) -> _CaseScript:
    observed_at = _BASE_TIME + timedelta(seconds=index * 10)
    lookup = _adapter_result(
        index=index,
        observed_at=observed_at,
        provider=provider,
    )
    workflow_id = _opaque_id("workflow", "phase5-property", seed, index)
    initial = new_workflow(
        workflow_id=workflow_id,
        started_at=observed_at - timedelta(seconds=1),
    )
    state = initial
    events: list[tuple[object, tuple[object, ...]]] = []
    fixed_events = (
        StartSearch(
            event_id=_opaque_id("event", workflow_id, "search"),
            occurred_at=observed_at,
            query=lookup.query,
        ),
        LookupRecorded(
            event_id=_opaque_id("event", workflow_id, "lookup"),
            occurred_at=observed_at + timedelta(seconds=1),
            evidence=lookup.evidence,
            offers=lookup.offers,
        ),
        OfferChosen(
            event_id=_opaque_id("event", workflow_id, "offer"),
            occurred_at=observed_at + timedelta(seconds=2),
            offer_id=lookup.offers[0].offer_id,
        ),
        DraftRequested(
            event_id=_opaque_id("event", workflow_id, "draft"),
            occurred_at=observed_at + timedelta(seconds=3),
            draft_id=_opaque_id("draft", workflow_id),
            customer=CustomerFacts(
                customer_ref=_opaque_id("customer", workflow_id),
                full_name=f"Synthetic Property Person {index}",
                email=(
                    f"synthetic.phase5.{index}"
                    + chr(64)
                    + "example.invalid"
                ),
                phone_e164="+999" + f"{index % 100_000_000:08d}",
                country_code="ZZ",
            ),
            terms=EconomicTerms(payment_method="card"),
        ),
    )
    for event in fixed_events:
        transition = reduce(state, event)
        if transition.commands:
            raise AssertionError("pre-confirmation transition emitted a command")
        state = transition.state
        events.append((event, ()))
    if type(state) is not ReadyToSummarizeState:
        raise AssertionError("property workflow did not reach ready_to_summarize")
    locale = SummaryLocale.PT_BR if provider is ProviderKind.CLOUDBEDS else SummaryLocale.EN
    prepared = prepare_summary(
        state,
        locale=locale,
        presented_at=observed_at + timedelta(seconds=4),
    )
    transition = reduce(state, prepared.event)
    if transition.commands:
        raise AssertionError("summary transition emitted a command")
    state = transition.state
    events.append(
        (
            prepared.event,
            (summary_outbox_message(workflow_id=workflow_id, prepared=prepared),),
        )
    )
    bound = classify_and_bind(
        state,
        source_event_id=_opaque_id("source", workflow_id),
        received_at=observed_at + timedelta(seconds=5),
        text="Pode fazer." if locale is SummaryLocale.PT_BR else "Go ahead.",
        locale=locale,
        content_hash=prepared.rendered.content_hash,
        classifier=ReferenceConfirmationClassifier(),
    )
    if bound.event is None:
        raise AssertionError("property confirmation was not bound")
    return _CaseScript(
        initial=initial,
        events=tuple((*events, (bound.event, ()))),
        confirmation_revision=state.meta.revision,
        confirmation_event=bound.event,
    )


def _actions_for_mode(mode: int) -> tuple[object, ...]:
    if mode == 0:
        return (ExecutionCertainty.EFFECT_CONFIRMED,)
    if mode == 1:
        return (ExecutionCertainty.CALLED_NO_EFFECT,)
    if mode == 2:
        return (ExecutionCertainty.CALLED_UNKNOWN,)
    if mode == 3:
        return (RuntimeError("synthetic dispatch exception"),)
    if mode == 4:
        return (
            PreparationFailure(
                reason="synthetic_timeout",
                retryable=True,
                evidence=(hashlib.sha256(b"phase5-property-retry").hexdigest(),),
            ),
            ExecutionCertainty.EFFECT_CONFIRMED,
        )
    if mode == 5:
        return (
            PreparationFailure(
                reason="unsupported_operation",
                retryable=False,
                evidence=(hashlib.sha256(b"phase5-property-terminal").hexdigest(),),
            ),
        )
    return (ExecutionCertainty.EFFECT_CONFIRMED,)


def _violate(violations: list[str], index: int, code: str) -> None:
    if len(violations) < 32:
        violations.append(f"case={index} {code}")


def _temporary_directory() -> tempfile.TemporaryDirectory[str]:
    shared_memory = Path("/dev/shm")
    if shared_memory.is_dir():
        try:
            return tempfile.TemporaryDirectory(
                prefix="phase5-properties-",
                dir=shared_memory,
            )
        except OSError:
            pass
    return tempfile.TemporaryDirectory(prefix="phase5-properties-")


def _persist_case(
    store: SQLiteUnitOfWork,
    script: _CaseScript,
    *,
    index: int,
    counters: dict[str, int],
    violations: list[str],
) -> tuple[str, str]:
    store.create_workflow(script.initial)
    workflow_id = script.initial.meta.workflow_id
    state = script.initial
    command_id = ""
    for offset, (event, outbox) in enumerate(script.events):
        persisted = store.apply_event(
            workflow_id,
            state.meta.revision,
            event,
            outbox=outbox,
        )
        state = persisted.state
        is_confirmation = offset == len(script.events) - 1
        if not is_confirmation and persisted.commands:
            counters["unauthorized_commands"] += len(persisted.commands)
            _violate(violations, index, "command_before_confirmation")
        if is_confirmation:
            counters["authorized_commands"] += len(persisted.commands)
            if len(persisted.commands) != 1:
                counters["second_commands"] += max(0, len(persisted.commands) - 1)
                _violate(violations, index, "authorized_command_cardinality")
            else:
                command_id = persisted.commands[0].command_id
    if not command_id:
        raise AssertionError("authorized property case did not persist a command")
    return workflow_id, command_id


def _probe_duplicate_and_conflict(
    store: SQLiteUnitOfWork,
    script: _CaseScript,
    *,
    workflow_id: str,
    index: int,
    counters: dict[str, int],
    violations: list[str],
) -> None:
    duplicate = store.apply_event(
        workflow_id,
        script.confirmation_revision,
        script.confirmation_event,
    )
    if duplicate.duplicate and not duplicate.commands:
        counters["duplicate_probes"] += 1
    else:
        _violate(violations, index, "duplicate_was_not_noop")
    divergent = replace(
        script.confirmation_event,
        occurred_at=script.confirmation_event.occurred_at + timedelta(microseconds=1),
    )
    try:
        store.apply_event(
            workflow_id,
            script.confirmation_revision,
            divergent,
        )
    except IdentityConflict:
        counters["conflict_probes"] += 1
    else:
        _violate(violations, index, "divergent_duplicate_accepted")


def _run_phase5_property_range(
    *,
    start: int,
    cases: int,
    seed: int,
    deep_consistency: bool = True,
) -> Phase5PropertyReport:
    if type(start) is not int or start < 0:
        raise ValueError("start must be a non-negative exact integer")
    if type(cases) is not int or cases < 1:
        raise ValueError("cases must be a positive exact integer")
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if type(deep_consistency) is not bool:
        raise TypeError("deep_consistency must be an exact bool")

    counters = {
        "cloudbeds_cases": 0,
        "bokun_cases": 0,
        **{field_name: 0 for field_name in _POSITIVE_FIELDS},
        **{field_name: 0 for field_name in _SAFETY_FIELDS},
    }
    outcome_counts = {key: 0 for key in _OUTCOME_KEYS}
    violations: list[str] = []
    end = start + cases
    execution_at = _BASE_TIME + timedelta(seconds=end * 10 + 600)
    pending_recoveries: dict[str, list[tuple[int, object]]] = {
        "cloudbeds": [],
        "bokun": [],
    }

    with _temporary_directory() as directory:
        base = Path(directory)
        stores = {
            provider: SQLiteUnitOfWork.open(base / f"{provider}.db")
            for provider in ("cloudbeds", "bokun")
        }
        try:
            indexes = tuple(range(start, end))
            ordered_indexes = tuple(
                sorted(
                    indexes,
                    key=lambda index: (
                        0 if index % 8 == 6 else 2 if index % 8 == 7 else 1,
                        index,
                    ),
                )
            )
            for index in ordered_indexes:
                mode = index % 8
                provider = (
                    ProviderKind.CLOUDBEDS
                    if (index + seed) % 2 == 0
                    else ProviderKind.BOKUN
                )
                provider_name = provider.value
                counters[f"{provider_name}_cases"] += 1
                store = stores[provider_name]
                try:
                    script = _build_case(index=index, seed=seed, provider=provider)
                    workflow_id, command_id = _persist_case(
                        store,
                        script,
                        index=index,
                        counters=counters,
                        violations=violations,
                    )
                    if mode == 7:
                        _probe_duplicate_and_conflict(
                            store,
                            script,
                            workflow_id=workflow_id,
                            index=index,
                            counters=counters,
                            violations=violations,
                        )
                        claim = store.claim_command(
                            worker_id=f"worker:expired:{index}",
                            now=execution_at,
                            lease_ttl=_LEASE_TTL,
                        )
                        if claim is None or claim.command.command_id != command_id:
                            raise AssertionError("expired-lease case claimed another command")
                        pending_recoveries[provider_name].append((index, claim))
                        continue

                    adapter = _ScriptedExecutionAdapter(_actions_for_mode(mode))
                    worker = CommandWorker(
                        store=store,
                        adapter=adapter,
                        worker_id=f"worker:property:{index}",
                        lease_ttl=_LEASE_TTL,
                    )
                    result = worker.run_once(now=execution_at)
                    if mode == 4:
                        if result.disposition is not WorkerDisposition.PREPARATION_REQUEUED:
                            _violate(violations, index, "retryable_prepare_not_requeued")
                        result = worker.run_once(now=execution_at + timedelta(seconds=1))
                    expected_disposition = (
                        WorkerDisposition.PREPARATION_TERMINAL
                        if mode == 5
                        else WorkerDisposition.COMPLETED
                    )
                    if result.disposition is not expected_disposition:
                        _violate(violations, index, "wrong_worker_disposition")
                    if adapter.command_id != command_id:
                        counters["wrong_command_claims"] += 1
                        _violate(violations, index, "worker_claimed_another_command")
                    counters["second_provider_calls"] += max(0, adapter.dispatch_calls - 1)
                    if mode == 6:
                        before_dispatches = adapter.dispatch_calls
                        target_message_id = store._connection.execute(
                            "SELECT message_id FROM outbox_messages WHERE command_id=?",
                            (command_id,),
                        ).fetchone()[0]
                        delivery = _ScriptedDelivery(
                            target_message_id=target_message_id,
                        )
                        outbox_worker = OutboxWorker(
                            store=store,
                            delivery=delivery,
                            worker_id=f"delivery:property:{index}",
                            lease_ttl=_LEASE_TTL,
                        )
                        pending = store._connection.execute(
                            "SELECT COUNT(*) FROM outbox_messages "
                            "WHERE status IN ('pending', 'leased')"
                        ).fetchone()[0]
                        target_failed = False
                        target_delivered = False
                        delivery_now = execution_at + timedelta(seconds=2)
                        for _ in range(pending + 2):
                            delivery.set_now(delivery_now)
                            delivery_result = outbox_worker.run_once(now=delivery_now)
                            if delivery_result.message_id == target_message_id:
                                if (
                                    delivery_result.disposition
                                    is OutboxWorkerDisposition.RETRYABLE_FAILURE
                                ):
                                    target_failed = True
                                elif (
                                    delivery_result.disposition
                                    is OutboxWorkerDisposition.DELIVERED
                                ):
                                    target_delivered = True
                                    break
                            delivery_now += timedelta(microseconds=1)
                        if target_failed and target_delivered and delivery.target_calls == 2:
                            counters["delivery_retries"] += 1
                            counters["delivery_target_matches"] += 1
                        else:
                            counters["wrong_delivery_targets"] += 1
                            _violate(violations, index, "delivery_retry_did_not_converge")
                        counters["outbox_provider_retries"] += (
                            adapter.dispatch_calls - before_dispatches
                        )
                except Exception as exc:
                    counters["unexpected_exceptions"] += 1
                    _violate(violations, index, f"unexpected:{type(exc).__name__}")

            for provider_name, recoveries in pending_recoveries.items():
                store = stores[provider_name]
                if recoveries:
                    reconciliation = Reconciler(store).run_once(
                        now=execution_at + _LEASE_TTL
                    )
                    counters["expired_lease_recoveries"] += (
                        reconciliation.pre_dispatch_released
                    )
                for index, stale_claim in recoveries:
                    request = DispatchRequest.from_command(
                        stale_claim.command,
                        dumps_command(stale_claim.command),
                    )
                    try:
                        store.fence_dispatch(
                            stale_claim,
                            request,
                            now=execution_at + _LEASE_TTL,
                        )
                    except StaleLease:
                        counters["stale_token_rejections"] += 1
                    else:
                        counters["stale_token_writes"] += 1
                        _violate(violations, index, "stale_claim_wrote_after_recovery")
                    adapter = _ScriptedExecutionAdapter(
                        (ExecutionCertainty.EFFECT_CONFIRMED,)
                    )
                    worker = CommandWorker(
                        store=store,
                        adapter=adapter,
                        worker_id=f"worker:recovered:{index}",
                        lease_ttl=_LEASE_TTL,
                    )
                    result = worker.run_once(
                        now=execution_at + _LEASE_TTL + timedelta(seconds=1)
                    )
                    if result.disposition is not WorkerDisposition.COMPLETED:
                        _violate(violations, index, "recovered_claim_not_completed")
                    if adapter.command_id == stale_claim.command.command_id:
                        counters["recovered_command_matches"] += 1
                    else:
                        counters["wrong_command_claims"] += 1
                        _violate(violations, index, "recovery_claimed_another_command")
                    counters["second_provider_calls"] += max(
                        0, adapter.dispatch_calls - 1
                    )

            for provider_name, store in stores.items():
                try:
                    quick_check = store._connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()[0]
                    foreign_keys = store._connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    if quick_check != "ok" or foreign_keys:
                        raise StoreError("SQLite structural consistency failed")
                    if deep_consistency:
                        store.assert_execution_consistency()
                        counters["consistency_probes"] += 1
                except StoreError:
                    counters["partial_transactions"] += 1
                    _violate(violations, end, f"{provider_name}_consistency_failure")
                idle_adapter = _ScriptedExecutionAdapter(
                    (RuntimeError("unexpected redispatch"),)
                )
                idle_worker = CommandWorker(
                    store=store,
                    adapter=idle_adapter,
                    worker_id=f"worker:idle-probe:{provider_name}",
                    lease_ttl=_LEASE_TTL,
                )
                idle = idle_worker.run_once(
                    now=execution_at + _LEASE_TTL + timedelta(seconds=2)
                )
                if idle.disposition is not WorkerDisposition.IDLE:
                    counters["unknown_redispatches"] += 1
                    _violate(violations, end, f"{provider_name}_terminal_redispatch")

                rows = store._connection.execute(
                    "SELECT c.workflow_id, l.status, l.dispatch_slots_consumed, "
                    "l.outcome_json FROM reservation_commands AS c "
                    "JOIN execution_ledger AS l ON l.command_id=c.command_id"
                ).fetchall()
                counters["terminal_commands"] += sum(
                    row[1]
                    in {LedgerStatus.OUTCOME_RECORDED.value, LedgerStatus.MANUAL_REVIEW.value}
                    for row in rows
                )
                counters["manual_reviews"] += sum(
                    row[1] == LedgerStatus.MANUAL_REVIEW.value for row in rows
                )
                counters["second_dispatch_slots"] += sum(
                    max(0, row[2] - 1) for row in rows
                )
                counters["missing_terminals"] += sum(
                    row[1]
                    not in {LedgerStatus.OUTCOME_RECORDED.value, LedgerStatus.MANUAL_REVIEW.value}
                    for row in rows
                )
                for _, _, _, outcome_json in rows:
                    if outcome_json is None:
                        continue
                    outcome = loads_outcome(outcome_json)
                    outcome_counts[outcome.certainty.value] += 1
                    if outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN:
                        counters["post_fence_unknowns"] += 1
                counters["summary_outboxes"] += store._connection.execute(
                    "SELECT COUNT(*) FROM outbox_messages WHERE kind=?",
                    (OutboxKind.SUMMARY_PRESENTED.value,),
                ).fetchone()[0]
                counters["final_outboxes"] += store._connection.execute(
                    "SELECT COUNT(*) FROM outbox_messages "
                    "WHERE command_id IS NOT NULL"
                ).fetchone()[0]
                command_rows = store._connection.execute(
                    "SELECT workflow_id, COUNT(*) FROM reservation_commands "
                    "GROUP BY workflow_id"
                ).fetchall()
                counters["second_commands"] += sum(
                    max(0, count - 1) for _, count in command_rows
                )
        finally:
            for store in stores.values():
                store.close()

    return Phase5PropertyReport(
        cases=cases,
        seed=seed,
        cloudbeds_cases=counters["cloudbeds_cases"],
        bokun_cases=counters["bokun_cases"],
        outcome_counts=outcome_counts,
        **{field_name: counters[field_name] for field_name in _POSITIVE_FIELDS},
        **{field_name: counters[field_name] for field_name in _SAFETY_FIELDS},
        violations=tuple(violations),
    )


def _merge_phase5_property_reports(
    reports: tuple[Phase5PropertyReport, ...],
    *,
    cases: int,
    seed: int,
) -> Phase5PropertyReport:
    if not reports:
        raise ValueError("reports must not be empty")
    if sum(report.cases for report in reports) != cases:
        raise ValueError("report case counts do not match the requested total")
    if any(report.seed != seed for report in reports):
        raise ValueError("all report seeds must match")
    return Phase5PropertyReport(
        cases=cases,
        seed=seed,
        cloudbeds_cases=sum(report.cloudbeds_cases for report in reports),
        bokun_cases=sum(report.bokun_cases for report in reports),
        outcome_counts={
            key: sum(report.outcome_counts[key] for report in reports)
            for key in _OUTCOME_KEYS
        },
        **{
            field_name: sum(getattr(report, field_name) for report in reports)
            for field_name in (*_POSITIVE_FIELDS, *_SAFETY_FIELDS)
        },
        violations=tuple(
            violation
            for report in reports
            for violation in report.violations
        ),
    )


def run_phase5_properties(*, cases: int, seed: int) -> Phase5PropertyReport:
    return _run_phase5_property_range(
        start=0,
        cases=cases,
        seed=seed,
        deep_consistency=True,
    )


__all__ = ["Phase5PropertyReport", "run_phase5_properties"]
