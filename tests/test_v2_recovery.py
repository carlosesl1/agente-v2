from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from reservation_domain import ExecutionCertainty, ServiceKind
from reservation_followup.handoff import HandoffReasonCode
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_application.recovery import (
    CommercialEffectBlocked,
    HandoffCoordinator,
    HandoffEffectGuard,
    PackageComponent,
    PackageProgressStatus,
    PackageRecoveryPolicy,
)
from v2_contracts.payments import (
    BusinessUnit,
    DueKind,
    PaymentObligation,
)


NOW = datetime(2026, 7, 23, 19, 0, tzinfo=timezone.utc)
LEAD_ID = "manychat:subscriber-recovery-001"
WORKFLOW_ID = "workflow:package-recovery-001"


def _component(
    service: ServiceKind,
    certainty: ExecutionCertainty,
) -> PackageComponent:
    unit = BusinessUnit.HOSTEL if service is ServiceKind.LODGING else BusinessUnit.AGENCY
    return PackageComponent(
        command_id=f"command:{service.value}:recovery-001",
        service=service,
        business_unit=unit,
        certainty=certainty,
    )


def _obligation(unit: BusinessUnit) -> PaymentObligation:
    return PaymentObligation(
        payment_id=f"payment:{unit.value}:recovery-001",
        reservation_anchor_id=f"anchor:{unit.value}:recovery-001",
        business_unit=unit,
        amount_minor=10_000 if unit is BusinessUnit.HOSTEL else 20_000,
        currency="BRL",
        due_kind=DueKind.PREPAYMENT,
        economic_version=1,
        receiver_profile_id=f"receiver:{unit.value}:recovery-001",
    )


def _obligations() -> tuple[PaymentObligation, PaymentObligation]:
    return (_obligation(BusinessUnit.HOSTEL), _obligation(BusinessUnit.AGENCY))


def _required_receipts() -> frozenset[str]:
    return frozenset(
        {
            "receipt:reservation:hostel",
            "receipt:reservation:agency",
            "receipt:payment:hostel",
            "receipt:payment:agency",
        }
    )


def test_package_progress_is_derived_from_components_payments_and_receipts() -> None:
    components = (
        _component(ServiceKind.LODGING, ExecutionCertainty.EFFECT_CONFIRMED),
        _component(ServiceKind.ACTIVITY, ExecutionCertainty.EFFECT_CONFIRMED),
    )
    obligations = _obligations()
    required = _required_receipts()

    progress = PackageRecoveryPolicy().derive(
        components=components,
        obligations=obligations,
        settled_payment_ids=frozenset(item.payment_id for item in obligations),
        required_receipts=required,
        observed_receipts=required,
    )

    assert progress.status is PackageProgressStatus.COMPLETED
    assert progress.confirmed_command_ids == frozenset(
        item.command_id for item in components
    )
    assert progress.dispatchable_command_ids == frozenset()
    assert progress.missing_receipts == frozenset()
    assert progress.payment_claim_namespaces == (
        "payment-claim:hostel:payment:hostel:recovery-001",
        "payment-claim:agency:payment:agency:recovery-001",
    )


def test_package_restart_never_redispatches_confirmed_component() -> None:
    lodging = _component(ServiceKind.LODGING, ExecutionCertainty.EFFECT_CONFIRMED)
    activity = _component(ServiceKind.ACTIVITY, ExecutionCertainty.NOT_CALLED)
    policy = PackageRecoveryPolicy()

    before_restart = policy.derive(
        components=(lodging, activity),
        obligations=_obligations(),
        settled_payment_ids=frozenset(),
        required_receipts=_required_receipts(),
        observed_receipts=frozenset({"receipt:reservation:hostel"}),
    )
    after_restart = PackageRecoveryPolicy().derive(
        components=(lodging, activity),
        obligations=_obligations(),
        settled_payment_ids=frozenset(),
        required_receipts=_required_receipts(),
        observed_receipts=frozenset({"receipt:reservation:hostel"}),
    )

    assert after_restart == before_restart
    assert lodging.command_id in after_restart.confirmed_command_ids
    assert lodging.command_id not in after_restart.dispatchable_command_ids
    assert after_restart.dispatchable_command_ids == frozenset({activity.command_id})
    assert after_restart.status is PackageProgressStatus.PENDING


def test_unknown_package_component_requires_manual_review_without_redispatch() -> None:
    lodging = _component(ServiceKind.LODGING, ExecutionCertainty.EFFECT_CONFIRMED)
    activity = _component(ServiceKind.ACTIVITY, ExecutionCertainty.CALLED_UNKNOWN)

    progress = PackageRecoveryPolicy().derive(
        components=(lodging, activity),
        obligations=_obligations(),
        settled_payment_ids=frozenset(),
        required_receipts=_required_receipts(),
        observed_receipts=frozenset({"receipt:reservation:hostel"}),
    )

    assert progress.status is PackageProgressStatus.MANUAL_REVIEW
    assert progress.uncertain_command_ids == frozenset({activity.command_id})
    assert progress.dispatchable_command_ids == frozenset()
    assert lodging.command_id in progress.confirmed_command_ids


def test_package_rejects_cross_business_unit_receiver_leakage() -> None:
    hostel, agency = _obligations()
    leaked = PaymentObligation(
        payment_id=agency.payment_id,
        reservation_anchor_id=agency.reservation_anchor_id,
        business_unit=agency.business_unit,
        amount_minor=agency.amount_minor,
        currency=agency.currency,
        due_kind=agency.due_kind,
        economic_version=agency.economic_version,
        receiver_profile_id=hostel.receiver_profile_id,
    )

    with pytest.raises(ValueError, match="receiver profiles"):
        PackageRecoveryPolicy().derive(
            components=(
                _component(ServiceKind.LODGING, ExecutionCertainty.EFFECT_CONFIRMED),
                _component(ServiceKind.ACTIVITY, ExecutionCertainty.EFFECT_CONFIRMED),
            ),
            obligations=(hostel, leaked),
            settled_payment_ids=frozenset(),
            required_receipts=frozenset(),
            observed_receipts=frozenset(),
        )


def test_discount_handoff_is_single_restart_safe_and_blocks_effects(tmp_path: Path) -> None:
    path = tmp_path / "followup.db"
    store = SQLiteFollowupUnitOfWork.open(path)
    coordinator = HandoffCoordinator(store=store)

    first = coordinator.open_exception_once(
        lead_id=LEAD_ID,
        workflow_id=WORKFLOW_ID,
        source_event_id="event:discount-request-001",
        reason_code=HandoffReasonCode.CUSTOMER_REQUESTED,
        now=NOW,
    )
    replay = coordinator.open_exception_once(
        lead_id=LEAD_ID,
        workflow_id=WORKFLOW_ID,
        source_event_id="event:discount-request-002",
        reason_code=HandoffReasonCode.CUSTOMER_REQUESTED,
        now=NOW,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.workflow == first.workflow
    assert store._connection.execute(
        "SELECT count(*) FROM handoff_workflows"
    ).fetchone() == (1,)
    assert store._connection.execute(
        "SELECT count(*) FROM handoff_outbox WHERE kind='customer_acknowledgement'"
    ).fetchone() == (1,)
    assert store._connection.execute(
        "SELECT count(*) FROM payment_commands"
    ).fetchone() == (0,)

    guard = HandoffEffectGuard(store=store)
    assert guard.allows_workflow(WORKFLOW_ID) is False
    with pytest.raises(CommercialEffectBlocked) as blocked:
        guard.assert_allowed(lead_id=LEAD_ID)
    assert blocked.value.handoff_id == first.workflow.request.handoff_id
    with pytest.raises(CommercialEffectBlocked):
        guard.assert_workflow_allowed(workflow_id=WORKFLOW_ID)

    store.close()
    reopened = SQLiteFollowupUnitOfWork.open(path)
    recovered = HandoffCoordinator(store=reopened).open_exception_once(
        lead_id=LEAD_ID,
        workflow_id=WORKFLOW_ID,
        source_event_id="event:discount-request-003",
        reason_code=HandoffReasonCode.CUSTOMER_REQUESTED,
        now=NOW,
    )
    assert recovered.created is False
    assert recovered.workflow == first.workflow
    assert reopened._connection.execute(
        "SELECT count(*) FROM handoff_workflows"
    ).fetchone() == (1,)
    assert reopened._connection.execute(
        "SELECT count(*) FROM handoff_outbox"
    ).fetchone() == (1,)
    reopened.close()
