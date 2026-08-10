from __future__ import annotations

from v2_application import active_execution
from v2_contracts.model import ModelProposal
from reservation_boundary.types import BoundaryState


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