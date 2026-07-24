from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from reservation_domain import dumps_command
from reservation_execution import DispatchRequest
from reservation_execution.reconciliation import Reconciler
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from tests.phase5_helpers import T0, persist_script, workflow_events
from v2_application.recovery import (
    HandoffCoordinator,
    ManualReviewHandoffProjector,
)


def test_expired_fence_opens_one_restart_safe_handoff_without_redispatch(
    tmp_path: Path,
) -> None:
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution.sqlite3")
    followup = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    try:
        workflow_id = "workflow:reconciliation-handoff"
        initial, events = workflow_events("cloudbeds", workflow_id=workflow_id)
        execution.create_workflow(initial)
        persist_script(execution, workflow_id, events)
        claim = execution.claim_command(
            worker_id="worker:crashed-after-fence",
            now=T0 + timedelta(minutes=1),
            lease_ttl=timedelta(seconds=5),
        )
        assert claim is not None
        request = DispatchRequest.from_command(
            claim.command,
            dumps_command(claim.command),
        )
        execution.fence_dispatch(
            claim,
            request,
            now=T0 + timedelta(minutes=1),
        )

        recovered = Reconciler(execution).run_once(
            now=T0 + timedelta(minutes=1, seconds=6)
        )
        projector = ManualReviewHandoffProjector(
            execution=execution,
            coordinator=HandoffCoordinator(store=followup),
            lead_id="manychat:1873018537",
        )
        first = projector.run_once(now=T0 + timedelta(minutes=1, seconds=7))
        replay = projector.run_once(now=T0 + timedelta(minutes=1, seconds=8))

        assert recovered.called_unknown == 1
        assert first.created == 1
        assert replay.created == 0
        assert replay.replayed == 1
        assert execution._connection.execute(
            "SELECT status,dispatch_slots_consumed FROM execution_ledger"
        ).fetchone() == ("manual_review", 1)
        assert followup._connection.execute(
            "SELECT count(*) FROM handoff_workflows"
        ).fetchone() == (1,)
        assert followup._connection.execute(
            "SELECT count(*) FROM handoff_outbox"
        ).fetchone() == (1,)
    finally:
        followup.close()
        execution.close()
