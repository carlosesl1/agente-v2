"""Receiver rotation cannot turn a persisted reservation into a new payment."""

from dataclasses import replace
from datetime import timedelta

import pytest

from reservation_domain import ExecutionCertainty
from tests.test_v2_outcome_projector import (
    NOW,
    _finish_next,
    _package_command,
    _persist,
    _stores,
)
from v2_application.outcome_projector import ReservationOutcomeProjector
from v2_application.reservations import ReservationAllocator
from v2_contracts.payments import BusinessUnit, PaymentMethod, PaymentSelection


def _rotated(execution, payments, method):
    return ReservationOutcomeProjector(
        execution=execution,
        payment_store=payments,
        receiver_profiles={
            unit: f"stripe-account:{unit.value}:live" for unit in BusinessUnit
        },
        enabled_methods=(method,),
    )


def _confirmed(execution, method, component):
    command = (
        ReservationAllocator()
        .allocate(_package_command(payment_method=method.value))
        .commands[component]
    )
    _persist(execution, (command,))
    _finish_next(
        execution,
        now=NOW + timedelta(seconds=1),
        certainty=ExecutionCertainty.EFFECT_CONFIRMED,
    )


@pytest.mark.parametrize("method", tuple(PaymentMethod))
@pytest.mark.parametrize("component", (0, 1))
def test_receiver_rotation_replays_persisted_selection_without_new_payment(
    tmp_path, method, component
):
    execution, payments, projector = _stores(tmp_path, enabled_methods=(method,))
    try:
        _confirmed(execution, method, component)
        assert projector.run_once(now=NOW + timedelta(seconds=2)).inserted == 1
        before = payments._connection.execute(
            "SELECT * FROM payment_initiations"
        ).fetchall()
        rotated = _rotated(execution, payments, method)
        for seconds in (3, 4):
            result = rotated.run_once(now=NOW + timedelta(seconds=seconds))
            assert result.inserted == 0
            assert result.replayed == 1
            assert (
                payments._connection.execute(
                    "SELECT * FROM payment_initiations"
                ).fetchall()
                == before
            )
    finally:
        payments.close()
        execution.close()


@pytest.mark.parametrize("component", (0, 1))
def test_first_projection_after_rotation_uses_current_receiver(tmp_path, component):
    method = PaymentMethod.STRIPE
    execution, payments, _ = _stores(tmp_path)
    try:
        _confirmed(execution, method, component)
        rotated = _rotated(execution, payments, method)
        assert rotated.run_once(now=NOW + timedelta(seconds=2)).inserted == 1
        raw = payments._connection.execute(
            "SELECT selection_json FROM payment_initiations"
        ).fetchone()[0]
        import json

        assert json.loads(raw)["obligation"]["receiver_profile_id"].endswith(":live")
    finally:
        payments.close()
        execution.close()


@pytest.mark.parametrize("corruption", ("different_amount", "ambiguous_receiver"))
def test_receiver_rotation_does_not_hide_conflicting_financial_facts(
    tmp_path, corruption
):
    execution, payments, projector = _stores(tmp_path)
    try:
        _confirmed(execution, PaymentMethod.STRIPE, 0)
        projector.run_once(now=NOW + timedelta(seconds=2))
        claim = payments.claim(
            worker_id="fixture",
            now=NOW + timedelta(seconds=3),
            lease_ttl=timedelta(seconds=30),
        )
        selection = claim.selection
        changes = (
            {
                "amount_minor": selection.obligation.amount_minor + 1,
                "display_details": replace(
                    selection.obligation.display_details,
                    reservation_total_minor=selection.obligation.amount_minor + 1,
                ),
            }
            if corruption == "different_amount"
            else {"receiver_profile_id": "stripe-account:hostel:another"}
        )
        other = replace(selection.obligation, **changes)
        payments.enqueue(
            PaymentSelection(other, selection.method), now=NOW + timedelta(seconds=4)
        )
        before = payments._connection.execute(
            "SELECT * FROM payment_initiations"
        ).fetchall()
        with pytest.raises(RuntimeError, match="persisted payment"):
            _rotated(execution, payments, PaymentMethod.STRIPE).run_once(
                now=NOW + timedelta(seconds=5)
            )
        assert (
            payments._connection.execute("SELECT * FROM payment_initiations").fetchall()
            == before
        )
    finally:
        payments.close()
        execution.close()
