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
        "schema": "v2-model-proposal-v1",
        "source_event_id": "batch:1",
        "intent": "inform",
        "reply_chunks": ["Olá"],
        "facts": [],
        "read_requests": [],
        "effect_proposals": [],
        "target_offer_id": None,
        "confirmed_summary_version": None,
    }

    def execute(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return Result((json.dumps(proposal) + "\n").encode())

    output = run(("hermes", "--profile", "leads"), _wire(), execute=execute)

    assert output.startswith(b"PHASE8_RESULT\x00")
    assert json.loads(output.split(b"\x00", 1)[1]) == proposal
    assert captured["command"][:3] == ("hermes", "--profile", "leads")
    assert captured["command"][3:7] == ("--toolsets", "", "--max-turns", "1")
    assert captured["command"][7] == "-z"
    assert "Do not call tools or perform effects" in captured["command"][8]
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
