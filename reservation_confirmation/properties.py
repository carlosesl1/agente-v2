"""Deterministic property probes for summary/confirmation authorization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import random
from types import MappingProxyType

from reservation_domain import (
    AddOn,
    AwaitingAdjustmentState,
    AwaitingConfirmationState,
    ConfirmationDecisionKind,
    ConfirmationReceived,
    CustomerFacts,
    DraftAdjusted,
    EconomicTerms,
    Money,
    OfferSnapshot,
    Party,
    ReadyToSummarizeState,
    ServiceKind,
    TransitionStatus,
    build_commercial_draft,
    new_workflow,
    reduce,
)

from .binding import classify_and_bind
from .classifier import ReferenceConfirmationClassifier
from .presentation import prepare_summary
from .types import SummaryLocale


@dataclass(frozen=True, slots=True)
class Phase4PropertyReport:
    cases: int
    seed: int
    locale_counts: Mapping[str, int]
    decision_counts: Mapping[str, int]
    authorized_accepts: int
    commands_emitted: int
    duplicate_probes: int
    adjustment_probes: int
    context_failure_probes: int
    artifact_tamper_probes: int
    classifier_failure_probes: int
    premature_commands: int
    second_commands: int
    duplicate_reemissions: int
    stale_confirmation_acceptances: int
    adjustment_disarm_failures: int
    context_failure_events: int
    unexpected_exceptions: int
    violations: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "cases",
            "seed",
            "authorized_accepts",
            "commands_emitted",
            "duplicate_probes",
            "adjustment_probes",
            "context_failure_probes",
            "artifact_tamper_probes",
            "classifier_failure_probes",
            "premature_commands",
            "second_commands",
            "duplicate_reemissions",
            "stale_confirmation_acceptances",
            "adjustment_disarm_failures",
            "context_failure_events",
            "unexpected_exceptions",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative exact integer")
        for name in ("locale_counts", "decision_counts"):
            value = getattr(self, name)
            if not isinstance(value, Mapping) or any(
                type(key) is not str or type(count) is not int or count < 0
                for key, count in value.items()
            ):
                raise ValueError(f"{name} must be a string-to-integer mapping")
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(sorted(value.items()))),
            )
        if type(self.violations) is not tuple or any(
            type(item) is not str for item in self.violations
        ):
            raise ValueError("violations must be an exact tuple of strings")

    @property
    def passed(self) -> bool:
        return bool(
            set(self.locale_counts) == {"pt_BR", "en"}
            and all(value > 0 for value in self.locale_counts.values())
            and set(self.decision_counts)
            == {"accept", "reject", "adjust", "ambiguous"}
            and all(value > 0 for value in self.decision_counts.values())
            and self.authorized_accepts > 0
            and self.commands_emitted == self.authorized_accepts
            and self.duplicate_probes > 0
            and self.adjustment_probes > 0
            and self.context_failure_probes > 0
            and self.artifact_tamper_probes > 0
            and self.classifier_failure_probes > 0
            and self.premature_commands == 0
            and self.second_commands == 0
            and self.duplicate_reemissions == 0
            and self.stale_confirmation_acceptances == 0
            and self.adjustment_disarm_failures == 0
            and self.context_failure_events == 0
            and self.unexpected_exceptions == 0
            and not self.violations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "cases": self.cases,
            "seed": self.seed,
            "locale_counts": dict(self.locale_counts),
            "decision_counts": dict(self.decision_counts),
            "authorized_accepts": self.authorized_accepts,
            "commands_emitted": self.commands_emitted,
            "duplicate_probes": self.duplicate_probes,
            "adjustment_probes": self.adjustment_probes,
            "context_failure_probes": self.context_failure_probes,
            "artifact_tamper_probes": self.artifact_tamper_probes,
            "classifier_failure_probes": self.classifier_failure_probes,
            "premature_commands": self.premature_commands,
            "second_commands": self.second_commands,
            "duplicate_reemissions": self.duplicate_reemissions,
            "stale_confirmation_acceptances": self.stale_confirmation_acceptances,
            "adjustment_disarm_failures": self.adjustment_disarm_failures,
            "context_failure_events": self.context_failure_events,
            "unexpected_exceptions": self.unexpected_exceptions,
            "violations": list(self.violations),
            "passed": self.passed,
        }


class _RaisingClassifier:
    def classify(self, item):
        raise RuntimeError("synthetic classifier failure")


def _offer(
    *,
    index: int,
    service: ServiceKind,
    party: Party,
    start_date: date,
) -> OfferSnapshot:
    suffix = "lodging" if service is ServiceKind.LODGING else "activity"
    return OfferSnapshot(
        offer_id=f"offer:phase4:{index}:{suffix}",
        lookup_id=f"lookup:phase4:{index}:{suffix}",
        service=service,
        provider_ref=f"synthetic.provider.{suffix}.{index}",
        public_label=(
            f"Synthetic room {index}"
            if service is ServiceKind.LODGING
            else f"Synthetic activity {index}"
        ),
        start_date=start_date,
        end_date=(
            start_date + timedelta(days=2)
            if service is ServiceKind.LODGING
            else None
        ),
        start_time=None if service is ServiceKind.LODGING else "08:00",
        party=party,
        total=Money(
            amount=Decimal(100 + (index % 50)).quantize(Decimal("0.01")),
            currency="BRL",
        ),
        available=True,
    )


def _ready_state(index: int, rng: random.Random) -> ReadyToSummarizeState:
    base = datetime(2027, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index * 10)
    party = Party(adults=1 + rng.randrange(3), children=rng.randrange(2))
    start_date = date(2027, 3, 1) + timedelta(days=index % 20)
    shape = index % 3
    services = (
        (ServiceKind.LODGING,)
        if shape == 0
        else (ServiceKind.ACTIVITY,)
        if shape == 1
        else (ServiceKind.LODGING, ServiceKind.ACTIVITY)
    )
    components = tuple(
        _offer(index=index, service=service, party=party, start_date=start_date)
        for service in services
    )
    add_ons = (
        (
            AddOn(
                code="synthetic_breakfast",
                quantity=1 + rng.randrange(3),
                unit_price=Money(amount=Decimal("30.00"), currency="BRL"),
            ),
        )
        if rng.randrange(2)
        else ()
    )
    draft = build_commercial_draft(
        draft_id=f"draft:phase4:{index}",
        version=1,
        created_at=base,
        components=components,
        customer=CustomerFacts(
            customer_ref=f"customer:phase4:{index}",
            full_name=f"Synthetic Property Person {index}",
            email=f"synthetic.phase4.{index}@example.invalid",
            phone_e164=f"+999{index:08d}",
            country_code="ZZ",
        ),
        terms=EconomicTerms(payment_method="card", add_ons=add_ons),
    )
    initial = new_workflow(
        workflow_id=f"workflow:phase4:{index}",
        started_at=base,
    )
    return ReadyToSummarizeState(meta=initial.meta, draft=draft)


def _text(mode: int, locale: SummaryLocale) -> str:
    pt = (
        "Sim, confirmo exatamente esse resumo.",
        "Pode fazer.",
        "Não confirme.",
        "Troque para cartão.",
        "Vou pensar e aviso.",
        "Pode fazer.",
        "Pode fazer.",
        "Pode fazer.",
        "Pode fazer.",
        "Fechado, pode seguir.",
        "Pode fazer.",
        "Sim, mas não confirme ainda.",
    )
    en = (
        "I confirm this exact summary.",
        "Go ahead.",
        "Do not confirm.",
        "Change it to card.",
        "Let me think about it.",
        "Go ahead.",
        "Go ahead.",
        "Go ahead.",
        "Go ahead.",
        "Sounds good, go ahead.",
        "Go ahead.",
        "Yes, but do not book it yet.",
    )
    return (pt if locale is SummaryLocale.PT_BR else en)[mode]


def run_confirmation_properties(*, cases: int, seed: int) -> Phase4PropertyReport:
    if type(cases) is not int or cases < 1:
        raise ValueError("cases must be a positive exact integer")
    if type(seed) is not int:
        raise ValueError("seed must be an exact integer")
    rng = random.Random(seed)
    locale_counts = {"pt_BR": 0, "en": 0}
    decision_counts = {item.value: 0 for item in ConfirmationDecisionKind}
    counters = {
        "authorized_accepts": 0,
        "commands_emitted": 0,
        "duplicate_probes": 0,
        "adjustment_probes": 0,
        "context_failure_probes": 0,
        "artifact_tamper_probes": 0,
        "classifier_failure_probes": 0,
        "premature_commands": 0,
        "second_commands": 0,
        "duplicate_reemissions": 0,
        "stale_confirmation_acceptances": 0,
        "adjustment_disarm_failures": 0,
        "context_failure_events": 0,
        "unexpected_exceptions": 0,
    }
    violations: list[str] = []

    def violation(index: int, code: str) -> None:
        if len(violations) < 20:
            violations.append(f"case={index} {code}")

    for index in range(cases):
        mode = index % 12
        locale = SummaryLocale.PT_BR if (index + rng.randrange(2)) % 2 == 0 else SummaryLocale.EN
        locale_counts[locale.value] += 1
        try:
            ready = _ready_state(index, rng)
            prepared = prepare_summary(
                ready,
                locale=locale,
                presented_at=ready.draft.created_at + timedelta(seconds=1),
            )
            awaiting = reduce(ready, prepared.event).state
            if not isinstance(awaiting, AwaitingConfirmationState):
                raise AssertionError("summary did not reach awaiting confirmation")

            binding_state: AwaitingConfirmationState | None = awaiting
            content_hash: str | None = prepared.rendered.content_hash
            received_at = prepared.presented_at + timedelta(seconds=1)
            classifier = ReferenceConfirmationClassifier()
            expected_eventless = mode in {5, 6, 7, 8, 10}
            if mode == 5:
                binding_state = None
            elif mode == 6:
                first = "0" if content_hash[0] != "0" else "1"
                content_hash = first + content_hash[1:]
            elif mode == 7:
                received_at = prepared.presented_at
            elif mode == 8:
                classifier = _RaisingClassifier()
                counters["classifier_failure_probes"] += 1
            elif mode == 10:
                counters["artifact_tamper_probes"] += 1
                binding_state = AwaitingConfirmationState(
                    meta=awaiting.meta,
                    draft=awaiting.draft,
                    summary=replace(
                        awaiting.summary,
                        outbox_message_id=f"outbox:tampered:{index}",
                    ),
                )
            if expected_eventless:
                counters["context_failure_probes"] += 1

            bound = classify_and_bind(
                binding_state,
                source_event_id=f"source:phase4:{index}",
                received_at=received_at,
                text=_text(mode, locale),
                locale=locale,
                content_hash=content_hash,
                classifier=classifier,
            )
            decision_counts[bound.candidate.decision.value] += 1
            if expected_eventless and bound.event is not None:
                counters["context_failure_events"] += 1
                violation(index, "context_failure_emitted_event")
            if bound.event is None:
                continue

            transition = reduce(awaiting, bound.event)
            commands = len(transition.commands)
            counters["commands_emitted"] += commands
            valid_accept = bound.candidate.decision is ConfirmationDecisionKind.ACCEPT
            if valid_accept:
                counters["authorized_accepts"] += 1
                if commands != 1:
                    violation(index, "authorized_accept_missing_exact_command")
            elif commands:
                counters["premature_commands"] += commands
                violation(index, "non_accept_emitted_command")
            if len(transition.state.command_ids) > 1:
                counters["second_commands"] += len(transition.state.command_ids) - 1
                violation(index, "more_than_one_command_id")

            counters["duplicate_probes"] += 1
            duplicate = reduce(transition.state, bound.event)
            if duplicate.commands:
                counters["duplicate_reemissions"] += len(duplicate.commands)
                violation(index, "duplicate_reemitted_command")

            if bound.candidate.decision is ConfirmationDecisionKind.ADJUST:
                counters["adjustment_probes"] += 1
                if not isinstance(transition.state, AwaitingAdjustmentState):
                    counters["adjustment_disarm_failures"] += 1
                    violation(index, "adjustment_did_not_disarm")
                    continue
                stale_old = ConfirmationReceived(
                    event_id=f"event:stale:disarmed:{index}",
                    occurred_at=received_at + timedelta(seconds=1),
                    confirmation_event_id=f"confirmation:stale:disarmed:{index}",
                    decision=ConfirmationDecisionKind.ACCEPT,
                    target_draft_version=awaiting.draft.version,
                    subject_signature=awaiting.draft.subject_signature,
                )
                stale_disarmed = reduce(transition.state, stale_old)
                if stale_disarmed.commands:
                    counters["stale_confirmation_acceptances"] += len(
                        stale_disarmed.commands
                    )
                    violation(index, "disarmed_summary_authorized")
                changed_customer = replace(
                    transition.state.draft.customer,
                    full_name=f"Synthetic Adjusted Person {index}",
                )
                adjusted = reduce(
                    stale_disarmed.state,
                    DraftAdjusted(
                        event_id=f"event:draft-adjusted:{index}",
                        occurred_at=received_at + timedelta(seconds=2),
                        customer=changed_customer,
                        terms=transition.state.draft.terms,
                    ),
                )
                if not isinstance(adjusted.state, ReadyToSummarizeState):
                    counters["adjustment_disarm_failures"] += 1
                    violation(index, "semantic_adjustment_did_not_create_v2")
                    continue
                prepared_v2 = prepare_summary(
                    adjusted.state,
                    locale=locale,
                    presented_at=received_at + timedelta(seconds=3),
                )
                awaiting_v2 = reduce(adjusted.state, prepared_v2.event).state
                stale_after_v2 = ConfirmationReceived(
                    event_id=f"event:stale:v2:{index}",
                    occurred_at=received_at + timedelta(seconds=4),
                    confirmation_event_id=f"confirmation:stale:v2:{index}",
                    decision=ConfirmationDecisionKind.ACCEPT,
                    target_draft_version=awaiting.draft.version,
                    subject_signature=awaiting.draft.subject_signature,
                )
                stale_transition = reduce(awaiting_v2, stale_after_v2)
                if stale_transition.commands:
                    counters["stale_confirmation_acceptances"] += len(
                        stale_transition.commands
                    )
                    violation(index, "old_version_authorized_after_v2_summary")
        except Exception as exc:
            counters["unexpected_exceptions"] += 1
            violation(index, f"exception={type(exc).__name__}:{exc}")

    return Phase4PropertyReport(
        cases=cases,
        seed=seed,
        locale_counts=locale_counts,
        decision_counts=decision_counts,
        violations=tuple(violations),
        **counters,
    )


__all__ = ["Phase4PropertyReport", "run_confirmation_properties"]
