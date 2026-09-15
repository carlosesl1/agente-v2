from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from v2_application import active_execution
from v2_application.reservations import ReservationAllocator
from v2_application.relay_worker import build_reservation_relay_bundle
from v2_contracts.model import ModelProposal
from reservation_boundary.types import BoundaryState
from reservation_domain import ExecutionCertainty, loads_state
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_outcome_projector import NOW, _finish_next, _package_command, _persist


def _state() -> BoundaryState:
    return BoundaryState(
        schema_version=7,
        lead_key="manychat:active-execution",
        version=4,
        workflow=None,
        handoff=None,
        payments=(),
        processed_event_ids=(),
    )


def _proposal(*, reply: str, question: str | None) -> ModelProposal:
    return ModelProposal(
        source_event_id="batch:active-execution",
        intent="inform",
        reply_chunks=(reply,),
        facts=(),
        read_requests=(),
        effect_proposals=(),
        clarification_question=question,
    )


def test_regressive_post_command_guard_uses_typed_question_not_punctuation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(active_execution, "active_execution_status", lambda state: "queued")

    assert active_execution.is_regressive_post_command_reply(
        _state(),
        _proposal(reply="Which option should I use", question="Which option should I use"),
    )
    assert not active_execution.is_regressive_post_command_reply(
        _state(),
        _proposal(reply="Already processing? Yes.", question=None),
    )


def test_durable_terminal_failure_overrides_stale_boundary_queue(
    tmp_path: Path,
) -> None:
    parent = _package_command()
    workflow = loads_state(
        build_reservation_relay_bundle(parent).expected_final_state.decode("utf-8")
    )
    state = replace(_state(), workflow=workflow)
    execution = SQLiteUnitOfWork.open_v6(tmp_path / "execution-status.sqlite3")
    try:
        commands = ReservationAllocator().allocate(parent).commands
        _persist(execution, commands)
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=1),
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
        )
        _finish_next(
            execution,
            now=NOW + timedelta(seconds=2),
            certainty=ExecutionCertainty.CALLED_NO_EFFECT,
        )

        status = active_execution.ReservationExecutionStatusResolver(
            execution
        ).resolve(state)

        assert active_execution.active_execution_status(state) == "queued"
        assert status == "failed_no_effect"
        assert not active_execution.blocks_active_commercial_progression(
            state,
            ModelProposal(
                source_event_id="batch:terminal-followup",
                intent="inform",
                reply_chunks=("Não foi possível concluir as reservas.",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
            ),
            execution_status=status,
        )
        assert active_execution.blocks_active_commercial_progression(
            state,
            ModelProposal(
                source_event_id="batch:terminal-retry",
                intent="select",
                reply_chunks=("Vou tentar reservar novamente.",),
                facts=(),
                read_requests=(),
                effect_proposals=(),
                target_offer_id="offer:" + "a" * 32,
            ),
            execution_status=status,
        )
    finally:
        execution.close()