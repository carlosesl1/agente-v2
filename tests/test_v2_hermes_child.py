from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from v2_host.hermes_child import run


@dataclass
class Result:
    stdout: bytes
    stderr: bytes = b""
    returncode: int = 0


def _wire() -> bytes:
    return json.dumps(
        {
            "system_prompt": "Return V2 JSON.",
            "messages": [["user", '{"source_event_id":"batch:1"}']],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_child_forces_tool_free_one_turn_and_emits_only_canonical_result() -> None:
    captured = {}
    proposal = {
        "schema": "v2-model-proposal-v3",
        "source_event_id": "batch:1",
        "intent": "inform",
        "reply_chunks": ["Olá"],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "target_offer_ids": [],
        "confirmed_summary_version": None,
        "confirmed_action_kinds": [],
        "approval_basis": None,
    }

    def execute(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return Result((json.dumps(proposal) + "\n").encode())

    output = run(("hermes", "--profile", "leads"), _wire(), execute=execute)

    assert output.startswith(b"PHASE8_RESULT\x00")
    assert json.loads(output.split(b"\x00", 1)[1]) == proposal
    assert captured["command"][:3] == ("hermes", "--profile", "leads")
    assert captured["command"][3:5] == ("--toolsets", "")
    assert captured["command"][5] == "-z"
    assert "Do not call tools or perform effects" in captured["command"][6]
    assert "matching the supplied system contract" in captured["command"][6]
    assert "v2-model-proposal-v" not in captured["command"][6]
    assert "--max-turns" not in captured["command"]
    assert "-q" not in captured["command"]
    assert captured["kwargs"] == {
        "capture_output": True,
        "timeout": 120,
        "check": False,
    }


def test_child_rejects_commentary_after_json() -> None:
    def execute(command, **kwargs):
        return Result(b'{"schema":"v2-model-proposal-v1"}\nextra')

    with pytest.raises(ValueError, match="one closed JSON"):
        run(("hermes",), _wire(), execute=execute)


def test_child_accepts_four_private_exchanges_and_keeps_current_request_last() -> None:
    messages = []
    for index in range(4):
        messages.extend(
            (["user", f"customer {index}"], ["assistant", f"assistant {index}"])
        )
    messages.append(["user", '{"message":"current"}'])
    wire = json.dumps(
        {"system_prompt": "Return V2 JSON.", "messages": messages},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    captured = {}

    def execute(command, **kwargs):
        captured["prompt"] = command[-1]
        return Result(b'{"schema":"v2-model-proposal-v1"}')

    run(("hermes",), wire, execute=execute)

    prompt = captured["prompt"]
    assert "PRIVATE COMMITTED DIALOGUE" in prompt
    assert "customer 0" in prompt
    assert "assistant 3" in prompt
    assert prompt.rfind('{"message":"current"}') > prompt.rfind("assistant 3")


@pytest.mark.parametrize(
    "messages",
    (
        [["assistant", "wrong first role"], ["user", "current"]],
        [["user", "history"], ["user", "current"]],
        [
            *sum(
                (
                    [
                        ["user", f"customer {index}"],
                        ["assistant", f"assistant {index}"],
                    ]
                    for index in range(5)
                ),
                [],
            ),
            ["user", "current"],
        ],
    ),
)
def test_child_rejects_noncanonical_or_unbounded_dialogue(messages) -> None:
    wire = json.dumps(
        {"system_prompt": "Return V2 JSON.", "messages": messages},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    with pytest.raises(ValueError, match="messages wire is invalid"):
        run(("hermes",), wire, execute=lambda *args, **kwargs: None)
