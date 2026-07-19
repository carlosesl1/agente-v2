"""Deterministic property probes for summary/confirmation authorization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
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
    DraftRequested,
    EconomicTerms,
    LookupRecorded,
    Money,
    OfferChosen,
    ReadyToSummarizeState,
    StartSearch,
    TransitionStatus,
    new_workflow,
    reduce,
)
from reservation_lookup import ProviderKind
from reservation_lookup.properties import _adapter_result

from .binding import classify_and_bind
from .classifier import ReferenceConfirmationClassifier
from .presentation import prepare_summary
from .types import SummaryLocale


_COVERAGE_FIELDS = (
    "cloudbeds_cases",
    "bokun_cases",
    "pt_cases",
    "en_cases",
    "explicit_cases",
    "colloquial_cases",
    "contextual_cases",
    "negative_cases",
    "ambiguous_cases",
    "adjust_cases",
    "deterministic_summaries",
    "private_field_safe_summaries",
    "posterior_accept_commands",
    "same_time_rejections",
    "stale_version_rejections",
    "context_free_rejections",
    "adjustment_disarms",
    "semantic_version_increments",
    "noop_adjustment_rejections",
    "duplicate_zero_additional",
    "classifier_error_rejections",
)


@dataclass(frozen=True, slots=True)
class Phase4PropertyReport:
    cases: int
    seed: int
    cloudbeds_cases: int
    bokun_cases: int
    pt_cases: int
    en_cases: int
    explicit_cases: int
    colloquial_cases: int
    contextual_cases: int
    negative_cases: int
    ambiguous_cases: int
    adjust_cases: int
    deterministic_summaries: int
    private_field_safe_summaries: int
    posterior_accept_commands: int
    same_time_rejections: int
    stale_version_rejections: int
    context_free_rejections: int
    adjustment_disarms: int
    semantic_version_increments: int
    noop_adjustment_rejections: int
    duplicate_zero_additional: int
    classifier_error_rejections: int
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
    false_commands: int
    missing_required_commands: int
    unexpected_exceptions: int
    violations: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "cases",
            "seed",
            *_COVERAGE_FIELDS,
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
            "false_commands",
            "missing_required_commands",
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
            and all(getattr(self, name) > 0 for name in _COVERAGE_FIELDS)
            and self.cloudbeds_cases + self.bokun_cases == self.cases
            and self.pt_cases + self.en_cases == self.cases
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
            and self.false_commands == 0
            and self.missing_required_commands == 0
            and self.unexpected_exceptions == 0
            and not self.violations
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "cases": self.cases,
            "seed": self.seed,
            **{name: getattr(self, name) for name in _COVERAGE_FIELDS},
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
            "false_commands": self.false_commands,
            "missing_required_commands": self.missing_required_commands,
            "unexpected_exceptions": self.unexpected_exceptions,
            "violations": list(self.violations),
            "passed": self.passed,
        }


class _RaisingClassifier:
    def classify(self, item):
        raise RuntimeError("synthetic classifier failure")


def _ready_state(
    index: int,
    seed: int,
    rng: random.Random,
) -> tuple[ReadyToSummarizeState, ProviderKind]:
    base = datetime(2027, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=index * 10)
    provider = (
        ProviderKind.CLOUDBEDS
        if (index + seed) % 2 == 0
        else ProviderKind.BOKUN
    )
    observed_at = base + timedelta(seconds=1)
    lookup = _adapter_result(
        index=index,
        observed_at=observed_at,
        provider=provider,
    )
    state = new_workflow(
        workflow_id=f"workflow:phase4:{index}",
        started_at=base,
    )
    state = reduce(
        state,
        StartSearch(
            event_id=f"event:search:{index}",
            occurred_at=observed_at,
            query=lookup.query,
        ),
    ).state
    state = reduce(
        state,
        LookupRecorded(
            event_id=f"event:lookup:{index}",
            occurred_at=observed_at + timedelta(seconds=1),
            evidence=lookup.evidence,
            offers=lookup.offers,
        ),
    ).state
    state = reduce(
        state,
        OfferChosen(
            event_id=f"event:offer:{index}",
            occurred_at=observed_at + timedelta(seconds=2),
            offer_id=lookup.offers[0].offer_id,
        ),
    ).state
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
    transition = reduce(
        state,
        DraftRequested(
            event_id=f"event:draft:{index}",
            occurred_at=observed_at + timedelta(seconds=3),
            draft_id=f"draft:phase4:{index}",
            customer=CustomerFacts(
                customer_ref=f"customer:phase4:{index}",
                full_name=f"Synthetic Property Person {index}",
                email="synthetic.phase4."
                + str(index)
                + chr(64)
                + "example.invalid",
                phone_e164=f"+999{index:08d}",
                country_code="ZZ",
            ),
            terms=EconomicTerms(payment_method="card", add_ons=add_ons),
        ),
    )
    if type(transition.state) is not ReadyToSummarizeState:
        raise AssertionError("adapter baseline did not reach ready_to_summarize")
    return transition.state, provider


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


_MODE_CATEGORY_FIELDS = (
    "explicit_cases",
    "contextual_cases",
    "negative_cases",
    "adjust_cases",
    "ambiguous_cases",
    "contextual_cases",
    "contextual_cases",
    "contextual_cases",
    "contextual_cases",
    "colloquial_cases",
    "contextual_cases",
    "ambiguous_cases",
)


def run_phase4_properties(*, cases: int, seed: int) -> Phase4PropertyReport:
    if type(cases) is not int or cases < 1:
        raise ValueError("cases must be a positive exact integer")
    if type(seed) is not int:
        raise ValueError("seed must be an exact integer")
    rng = random.Random(seed)
    locale_counts = {"pt_BR": 0, "en": 0}
    decision_counts = {item.value: 0 for item in ConfirmationDecisionKind}
    counters = {name: 0 for name in _COVERAGE_FIELDS}
    counters.update({
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
        "false_commands": 0,
        "missing_required_commands": 0,
        "unexpected_exceptions": 0,
    })
    violations: list[str] = []

    def violation(index: int, code: str) -> None:
        if len(violations) < 20:
            violations.append(f"case={index} {code}")

    for index in range(cases):
        mode = index % 12
        locale = SummaryLocale.PT_BR if (index + rng.randrange(2)) % 2 == 0 else SummaryLocale.EN
        locale_counts[locale.value] += 1
        counters["pt_cases" if locale is SummaryLocale.PT_BR else "en_cases"] += 1
        counters[_MODE_CATEGORY_FIELDS[mode]] += 1
        try:
            ready, provider = _ready_state(index, seed, rng)
            counters[f"{provider.value}_cases"] += 1
            prepared = prepare_summary(
                ready,
                locale=locale,
                presented_at=ready.draft.created_at + timedelta(seconds=1),
            )
            repeated = prepare_summary(
                ready,
                locale=locale,
                presented_at=ready.draft.created_at + timedelta(seconds=1),
            )
            if repeated == prepared:
                counters["deterministic_summaries"] += 1
            else:
                violation(index, "summary_not_deterministic")
            private_values = (
                ready.draft.draft_id,
                ready.draft.customer.customer_ref,
                *(component.offer_id for component in ready.draft.components),
                *(component.lookup_id for component in ready.draft.components),
                *(component.provider_ref for component in ready.draft.components),
            )
            if any(value in prepared.rendered.content for value in private_values):
                violation(index, "private_field_in_summary")
            else:
                counters["private_field_safe_summaries"] += 1
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
                if mode == 5:
                    counters["context_free_rejections"] += 1
                elif mode == 7:
                    counters["same_time_rejections"] += 1
                elif mode == 8:
                    counters["classifier_error_rejections"] += 1
                continue

            transition = reduce(awaiting, bound.event)
            commands = len(transition.commands)
            counters["commands_emitted"] += commands
            valid_accept = bound.candidate.decision is ConfirmationDecisionKind.ACCEPT
            if valid_accept:
                counters["authorized_accepts"] += 1
                if commands != 1:
                    counters["missing_required_commands"] += 1
                    violation(index, "authorized_accept_missing_exact_command")
                else:
                    counters["posterior_accept_commands"] += 1
            elif commands:
                counters["premature_commands"] += commands
                counters["false_commands"] += commands
                violation(index, "non_accept_emitted_command")
            if len(transition.state.command_ids) > 1:
                extra = len(transition.state.command_ids) - 1
                counters["second_commands"] += extra
                counters["false_commands"] += extra
                violation(index, "more_than_one_command_id")

            counters["duplicate_probes"] += 1
            duplicate = reduce(transition.state, bound.event)
            if duplicate.commands:
                counters["duplicate_reemissions"] += len(duplicate.commands)
                counters["false_commands"] += len(duplicate.commands)
                violation(index, "duplicate_reemitted_command")
            else:
                counters["duplicate_zero_additional"] += 1

            if bound.candidate.decision is ConfirmationDecisionKind.ADJUST:
                counters["adjustment_probes"] += 1
                if not isinstance(transition.state, AwaitingAdjustmentState):
                    counters["adjustment_disarm_failures"] += 1
                    violation(index, "adjustment_did_not_disarm")
                    continue
                counters["adjustment_disarms"] += 1
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
                    emitted = len(stale_disarmed.commands)
                    counters["stale_confirmation_acceptances"] += emitted
                    counters["false_commands"] += emitted
                    violation(index, "disarmed_summary_authorized")
                noop = reduce(
                    stale_disarmed.state,
                    DraftAdjusted(
                        event_id=f"event:draft-noop:{index}",
                        occurred_at=received_at + timedelta(seconds=2),
                        customer=transition.state.draft.customer,
                        terms=transition.state.draft.terms,
                    ),
                )
                if (
                    noop.status is TransitionStatus.REJECTED
                    and isinstance(noop.state, AwaitingAdjustmentState)
                    and noop.state.draft.version == awaiting.draft.version
                ):
                    counters["noop_adjustment_rejections"] += 1
                else:
                    counters["adjustment_disarm_failures"] += 1
                    violation(index, "noop_adjustment_created_version_or_armed_state")
                    continue
                changed_customer = replace(
                    transition.state.draft.customer,
                    full_name=f"Synthetic Adjusted Person {index}",
                )
                adjusted = reduce(
                    noop.state,
                    DraftAdjusted(
                        event_id=f"event:draft-adjusted:{index}",
                        occurred_at=received_at + timedelta(seconds=3),
                        customer=changed_customer,
                        terms=transition.state.draft.terms,
                    ),
                )
                if not isinstance(adjusted.state, ReadyToSummarizeState):
                    counters["adjustment_disarm_failures"] += 1
                    violation(index, "semantic_adjustment_did_not_create_v2")
                    continue
                if adjusted.state.draft.version == awaiting.draft.version + 1:
                    counters["semantic_version_increments"] += 1
                else:
                    violation(index, "semantic_adjustment_wrong_version_increment")
                prepared_v2 = prepare_summary(
                    adjusted.state,
                    locale=locale,
                    presented_at=received_at + timedelta(seconds=4),
                )
                awaiting_v2 = reduce(adjusted.state, prepared_v2.event).state
                stale_after_v2 = ConfirmationReceived(
                    event_id=f"event:stale:v2:{index}",
                    occurred_at=received_at + timedelta(seconds=5),
                    confirmation_event_id=f"confirmation:stale:v2:{index}",
                    decision=ConfirmationDecisionKind.ACCEPT,
                    target_draft_version=awaiting.draft.version,
                    subject_signature=awaiting.draft.subject_signature,
                )
                stale_transition = reduce(awaiting_v2, stale_after_v2)
                if stale_transition.commands:
                    emitted = len(stale_transition.commands)
                    counters["stale_confirmation_acceptances"] += emitted
                    counters["false_commands"] += emitted
                    violation(index, "old_version_authorized_after_v2_summary")
                elif stale_transition.status is TransitionStatus.REJECTED:
                    counters["stale_version_rejections"] += 1
                else:
                    violation(index, "old_version_not_rejected_after_v2_summary")
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


__all__ = ["Phase4PropertyReport", "run_phase4_properties"]
