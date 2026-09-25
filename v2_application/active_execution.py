from __future__ import annotations

from typing import Protocol
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from reservation_boundary.conversation import ConversationProjection
from reservation_boundary.types import BoundaryState, StringSlot
from reservation_domain import (
    AwaitingConfirmationState,
    ExecutingState,
    ExecutionCertainty,
    ExecutionQueuedState,
    ReservationOperation,
    ServiceKind,
    loads_outcome,
)
from reservation_execution import LedgerStatus
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.payment import VerifiedStripeEvent
from v2_application.lead_identity import payment_id_for_command
from v2_contracts.execution_context import (
    ComponentOutcome,
    ExecutedOffer,
    ExecutionComponentContext,
    ExecutionContext,
    PaymentSettlementContext,
)
from v2_contracts.model import ModelProposal
from v2_application.turn_plan import proposal_values
from v2_contracts.providers import ReadKind, ReadRequest


def active_execution_status(state: BoundaryState) -> str | None:
    if type(state) is not BoundaryState:
        raise TypeError("active execution status requires an exact BoundaryState")
    if type(state.workflow) is ExecutionQueuedState:
        return "queued"
    if type(state.workflow) is ExecutingState:
        return "executing"
    return None


class ExecutionStatusResolver(Protocol):
    def context(self, state: BoundaryState) -> ExecutionContext: ...


class ReservationExecutionStatusResolver:
    """Resolve one boundary command against its durable expanded execution group."""

    def __init__(
        self,
        execution: SQLiteUnitOfWork,
        *,
        payment_store=None,
        public_store=None,
        followup=None,
        lead_resolver=None,
        reservation_status_reader=None,
    ) -> None:
        if type(execution) is not SQLiteUnitOfWork:
            raise TypeError("execution must be exact SQLiteUnitOfWork")
        self._execution = execution
        self._payments = payment_store
        self._public = public_store
        self._followup = followup
        self._lead_resolver = lead_resolver
        self._reservation_status_reader = reservation_status_reader

    def resolve(self, state: BoundaryState) -> str | None:
        return self.context(state).status

    def context(self, state: BoundaryState) -> ExecutionContext:
        baseline = active_execution_status(state)
        parent = state.workflow.command if baseline is not None else None
        snapshot = self._execution.list_outcome_projection_inputs()
        current = tuple(
            (command, ledger)
            for command, ledger in snapshot
            if parent is not None
            and command.payload.customer.customer_ref
            == parent.payload.customer.customer_ref
            and command.draft_id == parent.draft_id
            and command.draft_version == parent.draft_version
        )
        # Productive composition supplies the durable owner, so old operations
        # remain visible even after the current workflow changes.
        owned = (
            tuple(
                (command, ledger)
                for command, ledger in snapshot
                if self._lead_resolver.lead_id_for_command(command.command_id)
                == state.lead_key
            )
            if self._lead_resolver is not None
            else current
        )
        components = [self._component(command, ledger) for command, ledger in owned]
        if parent is not None:
            present = {command.payload.components[0].offer_id for command, _ in current}
            for offer in parent.payload.components:
                if offer.offer_id not in present:
                    components.append(
                        ExecutionComponentContext(
                            None,
                            parent.draft_id,
                            parent.draft_version,
                            self._offer(offer),
                            baseline,
                            None,
                        )
                    )
        messages = (
            self._public.conversation_messages(state.lead_key) if self._public else ()
        )
        return ExecutionContext(
            self._status(parent, baseline, current) if parent is not None else None,
            tuple(components),
            messages,
        )

    @staticmethod
    def _offer(offer) -> ExecutedOffer:
        return ExecutedOffer(
            offer.offer_id,
            offer.service.value,
            offer.provider_ref,
            offer.public_label,
            offer.start_date,
            offer.end_date,
            offer.start_time,
            offer.party.adults,
            offer.party.children,
            format(offer.total.amount, "f"),
            offer.total.currency,
        )

    def _component(self, command, ledger) -> ExecutionComponentContext:
        payment_id = payment_id_for_command(command)
        payments = (
            self._payments.context_for_payment(payment_id) if self._payments else ()
        )
        workflows = (
            self._followup.payments_for_reservation(command.command_id)
            if self._followup
            else ()
        )
        settlements = tuple(
            PaymentSettlementContext(
                workflow.subject.payment_id,
                workflow.subject.payment_version,
                workflow.subject.amount_minor,
                workflow.subject.currency,
                workflow.subject.method.value if workflow.subject.method else None,
                workflow.status.value,
                workflow.settlement_finish.outcome.certainty.value
                if workflow.settlement_finish
                else None,
                evidence_basis=("visual_receipt" if workflow.evidence_record is not None and getattr(workflow.evidence_record.evidence, "human_review_status", None) == "pending" else None),
                human_review=("pending" if workflow.evidence_record is not None and getattr(workflow.evidence_record.evidence, "human_review_status", None) == "pending" else None),
                bank_settlement_confirmed=(False if workflow.evidence_record is not None and getattr(workflow.evidence_record.evidence, "human_review_status", None) == "pending" else None),
                stripe_capture_observed_at=(
                    workflow.evidence_record.evidence.observed_at
                    if workflow.verified_evidence is not None
                    and workflow.evidence_record is not None
                    and type(workflow.evidence_record.evidence) is VerifiedStripeEvent
                    else None
                ),
            )
            for workflow in workflows
        )
        settlement_status = (
            "unavailable"
            if self._followup is None
            else "recorded"
            if settlements
            else "not_recorded"
        )
        outcome = (
            loads_outcome(ledger.outcome_json)
            if ledger.outcome_json is not None
            else None
        )
        return ExecutionComponentContext(
            command.command_id,
            command.draft_id,
            command.draft_version,
            self._offer(command.payload.components[0]),
            ledger.status.value,
            ComponentOutcome(
                outcome.command_id,
                outcome.certainty.value,
                outcome.normalized_status,
                outcome.provider_reference,
            )
            if outcome
            else None,
            payment_id,
            (
                "unavailable"
                if self._payments is None
                else "recorded"
                if payments
                else "not_recorded"
            ),
            payments,
            settlement_status,
            settlements,
            reservation_status=(
                self._reservation_status_reader.read(
                    service=command.payload.components[0].service.value,
                    provider_reference=outcome.provider_reference,
                )
                if self._reservation_status_reader is not None
                and outcome is not None
                and outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
                else None
            ),
        )

    @staticmethod
    def _status(parent, baseline, members) -> str | None:
        expected_operations = {
            ReservationOperation.RESERVE_PACKAGE: frozenset(
                (
                    ReservationOperation.RESERVE_LODGING,
                    ReservationOperation.BOOK_ACTIVITY,
                )
            ),
            ReservationOperation.RESERVE_LODGING: frozenset(
                (ReservationOperation.RESERVE_LODGING,)
            ),
            ReservationOperation.BOOK_ACTIVITY: frozenset(
                (ReservationOperation.BOOK_ACTIVITY,)
            ),
        }[parent.operation]
        if (
            len(members) != len(expected_operations)
            or frozenset(command.operation for command, _ in members)
            != expected_operations
        ):
            return baseline
        statuses = frozenset(ledger.status for _, ledger in members)
        if (
            LedgerStatus.DISPATCH_FENCED in statuses
            or LedgerStatus.PREPARING in statuses
        ):
            return "executing"
        if LedgerStatus.QUEUED in statuses:
            return "queued"
        outcomes = tuple(
            loads_outcome(ledger.outcome_json)
            for _, ledger in members
            if ledger.outcome_json is not None
        )
        if len(outcomes) != len(members):
            return baseline
        if any(
            outcome.certainty is ExecutionCertainty.CALLED_UNKNOWN
            for outcome in outcomes
        ):
            return "uncertain"
        confirmed = sum(
            outcome.certainty is ExecutionCertainty.EFFECT_CONFIRMED
            for outcome in outcomes
        )
        if confirmed == len(outcomes):
            return "confirmed"
        if confirmed:
            return "partial_failure"
        if all(
            outcome.certainty is ExecutionCertainty.NOT_CALLED for outcome in outcomes
        ):
            return "failed_before_provider"
        return "failed_no_effect"


def _proposal_services(state: BoundaryState, proposal: ModelProposal, projection: ConversationProjection | None = None) -> frozenset[str]:
    if proposal.intent == "confirm":
        if type(state.workflow) is not AwaitingConfirmationState:
            return frozenset()  # Never renew by confirming the old command.
        return frozenset(c.service.value for c in state.workflow.draft.components)
    service = proposal_values(proposal, projection).get("service")
    return {
        "hostel": frozenset({"lodging"}),
        "agency": frozenset({"activity"}),
        "package": frozenset({"lodging", "activity"}),
    }.get(service, frozenset())


def _terminal_unpaid_component(component: ExecutionComponentContext, now: datetime) -> bool:
    observation = component.reservation_status
    if (
        component.command_id is None
        or component.outcome is None
        or component.outcome.certainty != "effect_confirmed"
        or not component.outcome.provider_reference
        or component.payment_initiation_status == "unavailable"
        or component.settlement_status == "unavailable"
        or observation is None
        or observation.source_status != "observed"
        or not timedelta(0) <= now - observation.observed_at <= timedelta(seconds=60)
    ):
        return False
    terminal = {
        "lodging": {"canceled", "cancelled"},
        "activity": {"TIMEOUT", "CANCELLED", "CANCELED", "ABORTED"},
    }
    if observation.reservation_status not in terminal.get(component.offer.service, set()):
        return False
    try:
        if Decimal(observation.paid_amount) != 0:
            return False
    except (InvalidOperation, TypeError, ValueError):
        return False
    if observation.payment_status not in {None, "NOT_PAID"}:
        return False
    if any(p.status != "completed" for p in component.payments):
        return False
    # A final pre-dispatch outcome cannot credit the old reservation. It may
    # still carry captured funds: keep that evidence/incident intact while a
    # NEW summary and separate confirmation create their own obligation.
    # Pending, uncertain, partial and settled outcomes never enter this branch.
    return all(
        (s.certainty == "not_dispatched" and s.status == "retryable")
        or (
            s.stripe_capture_observed_at is None
            and s.certainty is None
            and s.status in {
                "awaiting_method", "awaiting_financial_confirmation", "awaiting_evidence",
                "expired", "cancelled",
            }
        )
        for s in component.settlements
    )


def component_renewal_allowed(
    state: BoundaryState, proposal: ModelProposal, *,
    execution_context: ExecutionContext | None, now: datetime | None,
    projection: ConversationProjection | None = None,
) -> bool:
    """New selection/confirmation of a terminal unpaid service, never a replay.

    History stays in its existing stores. At confirmation, scope comes from the
    new draft rather than a model-provided service override.
    """
    if (
        type(execution_context) is not ExecutionContext
        or type(now) is not datetime or now.tzinfo is None
        or proposal.effect_proposals
        or not (proposal.intent in {"select", "confirm"} or proposal.selection_requested)
    ):
        return False
    services = _proposal_services(state, proposal, projection)
    components = tuple(c for c in execution_context.components if c.offer.service in services)
    if not services or {c.offer.service for c in components} != services:
        return False
    return all(_terminal_unpaid_component(c, now) for c in components)


def blocks_active_commercial_progression(
    state: BoundaryState,
    proposal: ModelProposal,
    *,
    execution_status: str | None = None,
    execution_context: ExecutionContext | None = None,
    now: datetime | None = None,
    projection: ConversationProjection | None = None,
) -> bool:
    """Preserve dispatched commands while permitting independently authorized renewal."""
    if type(state) is not BoundaryState or type(proposal) is not ModelProposal:
        raise TypeError("active progression guard requires exact V2 contracts")
    progress = bool(
        proposal.intent in {"select", "confirm", "adjust"}
        or proposal.target_offer_id is not None
        or proposal.target_offer_ids
        or proposal.selection_requested
        or proposal.effect_proposals
    )
    if not progress:
        return False
    status = execution_status or active_execution_status(state)
    if status is not None:
        return not component_renewal_allowed(
            state, proposal, execution_context=execution_context, now=now, projection=projection,
        )
    # Recheck retained history on the new draft, including after process restart.
    if execution_context is not None:
        services = _proposal_services(state, proposal, projection)
        historical = tuple(c for c in execution_context.components if c.offer.service in services)
        if historical:
            return not component_renewal_allowed(
                state, proposal, execution_context=execution_context, now=now, projection=projection,
            )
    return False


def _request_matches_active_draft(
    request: ReadRequest,
    *,
    state: BoundaryState,
    projection: ConversationProjection,
) -> bool:
    workflow = state.workflow
    if type(workflow) not in {ExecutionQueuedState, ExecutingState}:
        return False
    product_id = next(
        (
            fact.value.value
            for fact in projection.facts
            if fact.name == "product_id" and type(fact.value) is StringSlot
        ),
        None,
    )
    for component in workflow.draft.components:
        if (
            request.kind is ReadKind.LODGING
            and component.service is ServiceKind.LODGING
        ):
            if (
                component.end_date is not None
                and request.check_in == component.start_date
                and request.check_out == component.end_date
                and request.adults == component.party.adults
                and request.children == component.party.children
            ):
                return True
        if (
            request.kind is ReadKind.ACTIVITY
            and component.service is ServiceKind.ACTIVITY
        ):
            if (
                request.product_id == product_id
                and request.activity_date == component.start_date
                and request.activity_party()
                == (component.party.adults, component.party.children)
            ):
                return True
    return False


def is_redundant_post_command_read(
    state: BoundaryState,
    projection: ConversationProjection,
    proposal: ModelProposal,
    requests: tuple[ReadRequest, ...],
    *,
    execution_status: str | None = None,
) -> bool:
    status = execution_status or active_execution_status(state)
    if not requests or status is None:
        return False
    material_names = {
        "service",
        "product_id",
        "start_date",
        "end_date",
        "activity_date",
        "adults",
        "children",
        "payment_method",
    }
    if any(fact.name in material_names for fact in proposal.facts):
        return False
    return all(
        _request_matches_active_draft(item, state=state, projection=projection)
        for item in requests
    )
