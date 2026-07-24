from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from reservation_domain import (
    CommandPayload,
    CustomerFacts,
    EconomicTerms,
    ExecutionCertainty,
    Money,
    ReservationCommand,
    ReservationOperation,
    dumps_command,
)
from reservation_domain.signature import command_identity, subject_signature
from reservation_execution import DispatchRequest
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.phase5_helpers import _lookup
from v2_application.outcome_projector import ReservationOutcomeProjector
from v2_application.payments import SQLitePaymentInitiationStore
from v2_application.relay_worker import (
    build_reservation_relay_bundle,
    reservation_target_operation_id,
)
from v2_application.reservations import ReservationAllocator
from v2_contracts.payments import BusinessUnit

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
RESULT_KEY = b"outcome-projector-key-0000000001"


def _package_command(*, country_code: str = "BR") -> ReservationCommand:
    lodging = _lookup("cloudbeds").offers[0]
    activity = _lookup("bokun").offers[0]
    activity = activity.__class__(
        offer_id=activity.offer_id,
        lookup_id=activity.lookup_id,
        service=activity.service,
        provider_ref=activity.provider_ref,
        public_label=activity.public_label,
        start_date=activity.start_date,
        end_date=activity.end_date,
        start_time=activity.start_time,
        party=activity.party,
        total=Money(activity.total.amount, lodging.total.currency),
        available=activity.available,
    )
    components = (lodging, activity)
    customer = CustomerFacts(
        customer_ref="customer:outcome-projector",
        full_name="Pessoa Projector",
        email="projector@example.invalid",
        phone_e164="+12025550124",
        country_code=country_code,
        birth_date=datetime(1990, 1, 2).date(),
        gender="m",
    )
    terms = EconomicTerms(payment_method="stripe")
    signature = subject_signature(
        components=components,
        customer=customer,
        terms=terms,
    )
    command_id, idempotency_key = command_identity(
        workflow_id="workflow:outcome-projector-package",
        draft_id="draft:outcome-projector-package",
        draft_version=1,
        signature=signature,
        operation=ReservationOperation.RESERVE_PACKAGE,
    )
    return ReservationCommand(
        command_id=command_id,
        idempotency_key=idempotency_key,
        workflow_id="workflow:outcome-projector-package",
        draft_id="draft:outcome-projector-package",
        draft_version=1,
        subject_signature=signature,
        operation=ReservationOperation.RESERVE_PACKAGE,
        payload=CommandPayload(components, customer, terms),
        created_at=NOW,
    )


def _persist(store: SQLiteUnitOfWork, commands: tuple[ReservationCommand, ...]) -> None:
    for command in commands:
        bundle = build_reservation_relay_bundle(command)
        source_hash = hashlib.sha256(command.command_id.encode()).hexdigest()
        store.accept_boundary_reservation(
            operation_id=reservation_target_operation_id(
                bundle_hash=bundle.artifact_hash,
                source_turn_receipt_hash=source_hash,
            ),
            source_turn_receipt_hash=source_hash,
            bundle=bundle,
        )


def _finish_next(
    store: SQLiteUnitOfWork,
    *,
    now: datetime,
    certainty: ExecutionCertainty,
) -> ReservationCommand:
    claim = store.claim_command(
        worker_id="worker:projector-fixture",
        now=now,
        lease_ttl=timedelta(seconds=30),
    )
    assert claim is not None
    request = DispatchRequest.from_command(
        claim.command,
        dumps_command(claim.command),
    )
    permit = store.fence_dispatch(claim, request, now=now)
    outcome = claim.command.outcome(
        certainty=certainty,
        normalized_status=(
            "accepted"
            if certainty is ExecutionCertainty.EFFECT_CONFIRMED
            else "rejected"
        ),
        provider_reference=(
            hashlib.sha256(claim.command.command_id.encode()).hexdigest()
            if certainty is ExecutionCertainty.EFFECT_CONFIRMED
            else None
        ),
        evidence=(request.payload_hash,),
    )
    store.record_outcome(permit, outcome, now=now)
    return claim.command


def _stores(tmp_path: Path):
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    payments = SQLitePaymentInitiationStore(
        (tmp_path / "payments.sqlite3").resolve(),
        result_encryption_key=RESULT_KEY,
    )
    projector = ReservationOutcomeProjector(
        execution=execution,
        payment_store=payments,
        receiver_profiles={
            BusinessUnit.HOSTEL: "stripe-account:hostel:test",
            BusinessUnit.AGENCY: "stripe-account:agency:test",
        },
    )
    return execution, payments, projector


def test_single_reservation_projects_one_obligation(tmp_path: Path) -> None:
    execution, payments, projector = _stores(tmp_path)
    try:
        command = ReservationAllocator().allocate(_package_command()).commands[0]
        _persist(execution, (command,))
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )

        result = projector.run_once(now=NOW + timedelta(seconds=2))

        assert result.inserted == 1
        payload = json.loads(
            payments._connection.execute(
                "SELECT selection_json FROM payment_initiations"
            ).fetchone()[0]
        )
        assert payload["obligation"]["business_unit"] == "hostel"
        assert payload["obligation"]["receiver_profile_id"] == (
            "stripe-account:hostel:test"
        )
    finally:
        payments.close()
        execution.close()


def test_package_projects_two_unit_specific_obligations_once(tmp_path: Path) -> None:
    execution, payments, projector = _stores(tmp_path)
    try:
        commands = ReservationAllocator().allocate(_package_command()).commands
        _persist(execution, commands)
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=2),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )

        first = projector.run_once(now=NOW + timedelta(seconds=3))
        replay = projector.run_once(now=NOW + timedelta(seconds=4))

        assert first.inserted == 2
        assert replay.inserted == 0
        rows = payments._connection.execute(
            "SELECT selection_json FROM payment_initiations ORDER BY initiation_id"
        ).fetchall()
        assert len(rows) == 2
        payloads = tuple(json.loads(row[0]) for row in rows)
        assert {item["obligation"]["business_unit"] for item in payloads} == {
            "hostel",
            "agency",
        }
        assert {
            item["obligation"]["receiver_profile_id"] for item in payloads
        } == {
            "stripe-account:hostel:test",
            "stripe-account:agency:test",
        }
        expected_amounts = {
            int(component.total.amount * Decimal("100"))
            for component in _package_command().payload.components
        }
        assert {
            item["obligation"]["amount_minor"] for item in payloads
        } == expected_amounts
    finally:
        payments.close()
        execution.close()


def test_foreign_package_projects_agency_prepayment_only(tmp_path: Path) -> None:
    execution, payments, projector = _stores(tmp_path)
    try:
        commands = ReservationAllocator().allocate(
            _package_command(country_code="US")
        ).commands
        _persist(execution, commands)
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=2),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )

        result = projector.run_once(now=NOW + timedelta(seconds=3))

        assert result.inserted == 1
        payload = json.loads(
            payments._connection.execute(
                "SELECT selection_json FROM payment_initiations"
            ).fetchone()[0]
        )
        assert payload["obligation"]["business_unit"] == "agency"
    finally:
        payments.close()
        execution.close()


def test_incomplete_or_failed_package_projects_no_payment(tmp_path: Path) -> None:
    execution, payments, projector = _stores(tmp_path)
    try:
        commands = ReservationAllocator().allocate(_package_command()).commands
        _persist(execution, commands)
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.EFFECT_CONFIRMED,
        )

        pending = projector.run_once(now=NOW + timedelta(seconds=2))
        assert pending.inserted == 0
        assert pending.pending_groups == 1

        _finish_next(
            execution,
            now=NOW + timedelta(seconds=3),
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
        )
        failed = projector.run_once(now=NOW + timedelta(seconds=4))

        assert failed.inserted == 0
        assert failed.suppressed_groups == 1
        assert payments._connection.execute(
            "SELECT count(*) FROM payment_initiations"
        ).fetchone() == (0,)
    finally:
        payments.close()
        execution.close()
