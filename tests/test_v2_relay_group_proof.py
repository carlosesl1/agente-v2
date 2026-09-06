"""Review regressions: group proof is required for every source and target."""

from dataclasses import replace

import pytest

from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_commercial_reliability import _relay
from tests.test_v2_inbox_relay_workers import _source_receipt
from tests.test_v2_outcome_projector import NOW, _package_command
from v2_application.relay_worker import build_reservation_relay_bundle
from v2_application.reservations import ReservationAllocator


@pytest.mark.parametrize(
    "source_kind", ["no_proof", "partial_group", "changed_receipt"]
)
def test_any_relay_source_must_prove_complete_confirmed_membership(
    tmp_path, source_kind
):
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    commands = ReservationAllocator().allocate(_package_command()).commands
    worker = _relay(execution, commands)
    source = worker._boundary
    claim = source.claim
    proof, bundles = source.load_command_relay_group(claim)
    if source_kind == "no_proof":
        source.load_command_relay_group = lambda claim: (None, bundles)
    elif source_kind == "partial_group":
        source.load_command_relay_group = lambda claim: (proof, bundles[:1])
    else:
        changed = replace(proof, maya_proposal_hash="f" * 64, artifact_hash="")
        source.load_command_relay_group = lambda claim: (changed, bundles)
    try:
        with pytest.raises((TypeError, ValueError)):
            worker.run_once(now=NOW)
        assert execution.list_outcome_projection_inputs() == ()
        assert source.acks == []
    finally:
        execution.close()


def test_execution_owner_independently_rejects_partial_group(tmp_path):
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    commands = ReservationAllocator().allocate(_package_command()).commands
    bundles = tuple(build_reservation_relay_bundle(command) for command in commands)
    proof = _source_receipt(bundles, ("first", "second"))
    try:
        with pytest.raises(ValueError, match="incomplete"):
            execution.accept_boundary_reservation_group(
                source_receipt=proof, bundles=bundles[:1]
            )
        assert execution.list_outcome_projection_inputs() == ()
        receipts = execution.accept_boundary_reservation_group(
            source_receipt=proof, bundles=bundles
        )
        assert len(receipts) == len(commands)
        assert len(execution.list_outcome_projection_inputs()) == len(commands)
    finally:
        execution.close()
