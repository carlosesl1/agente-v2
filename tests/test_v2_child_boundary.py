"""Producer -> actual child process -> parser; no live agent or provider I/O."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from v2_adapters.hermes_model import HermesModelAdapter
from v2_contracts.model import ConversationExchange, InvalidModelProposal, ModelRequest
from v2_host.hermes_child import _MAX_INPUT, _closed_request


def request(exchanges: int = 0) -> ModelRequest:
    return ModelRequest(
        request_id="request:boundary",
        lead_id="manychat:boundary",
        source_event_id="batch:boundary",
        locale="pt-BR",
        state_version=exchanges,
        message="Corrigindo: 24 a 27 de novembro de 2026. Dois adultos, sem reservar.",
        recent_dialogue=tuple(
            ConversationExchange(
                f"Cliente {i}: ação", (f"Maya {i}: primeiro", "segundo")
            )
            for i in range(exchanges)
        ),
    )


@pytest.fixture
def child(tmp_path):
    """Only the external SDK is synthetic; main/parser/process/adapter are real."""
    (tmp_path / "hermes_cli").mkdir()
    (tmp_path / "hermes_cli/__init__.py").write_text("")
    (tmp_path / "hermes_cli/profiles.py").write_text(
        f"def resolve_profile_env(profile): return {str(tmp_path)!r}\n"
    )
    (tmp_path / "hermes_state.py").write_text(
        "class SessionDB:\n    def __init__(self, path): pass\n"
    )
    capture = tmp_path / "capture.json"
    root = Path(__file__).resolve().parents[1]
    command = (
        sys.executable,
        "-c",
        f"import sys; sys.path[:0] = {[str(tmp_path), str(root)]!r}; "
        "from v2_host.hermes_child import main; main()",
        "--profile",
        "leads",
        "--model",
        "synthetic",
        "--provider",
        "synthetic",
        "--reasoning-effort",
        "high",
    )

    def build(mode="valid"):
        (tmp_path / "run_agent.py").write_text(
            "import json\nfrom pathlib import Path\n"
            "class AIAgent:\n"
            "    def __init__(self, **kwargs):\n"
            "        assert kwargs['enabled_toolsets'] == []\n"
            "    def run_conversation(self, prompt, *, system_message):\n"
            f"        Path({str(capture)!r}).write_text(json.dumps([prompt, system_message]))\n"
            + (
                "        raise ValueError('synthetic execution failure')\n"
                if mode == "execution"
                else "        return {'final_response': 'not JSON'}\n"
                if mode == "output"
                else "        return {'final_response': '\\ud800'}\n"
                if mode == "surrogate"
                else "        return {'final_response': json.dumps({'intent':'inform',"
                "'reply_chunks':[{'text':'Resposta sintética','expects_reply':False}],"
                "'facts':[],'read_requests':[],'selected_choice_refs':[],"
                "'selection_requested':False,'pending_action_disposition':None,'passengers':[]})}\n"
            )
            + "    def close(self): pass\n"
        )
        calls = []

        def execute(cmd, **kwargs):
            calls.append(kwargs["input"])
            return subprocess.run(cmd, **kwargs)

        adapter = HermesModelAdapter(
            command=command,
            system_prompt="Return V8 JSON.",
            timeout=10,
            transcript_key=b"t" * 32,
            environ={},
            run=execute,
        )
        return adapter, command, calls, capture

    return build


@pytest.mark.parametrize("count", [0, 4, 5, 8, 12, 128])
def test_long_history_reaches_actual_child_unchanged(child, count):
    adapter, _, calls, capture = child()
    req = request(count)
    turn = adapter.complete_audited(req)
    assert len(calls) == 1
    assert turn.proposal.reply_chunks == ("Resposta sintética",)
    wire = json.loads(calls[0])
    assert len(wire["messages"]) == count * 2 + 1
    prompt, _ = json.loads(capture.read_text())
    current = wire["messages"][-1][1]
    assert prompt.endswith("CURRENT REQUEST JSON:\n" + current)
    if count:
        received = prompt.split("instructions):\n", 1)[1].split(
            "\n\nCURRENT REQUEST JSON:", 1
        )[0]
        assert json.loads(received) == wire["messages"][:-1]


def test_committed_operation_turn_with_no_customer_text_is_preserved(child):
    adapter, _, calls, capture = child()
    req = replace(
        request(),
        recent_dialogue=(ConversationExchange("", ("Resultado operacional",)),),
    )
    adapter.complete_audited(req)
    assert json.loads(calls[0])["messages"][-3:-1] == [
        ["user", ""],
        ["assistant", "Resultado operacional"],
    ]
    assert "Resultado operacional" in capture.read_text()


@pytest.mark.parametrize("delta", [0, 1])
def test_child_retains_exact_envelope_byte_limit(delta):
    value = {"system_prompt": "a", "messages": [["user", "atual"]]}
    raw = json.dumps(value, ensure_ascii=False).encode()
    value["system_prompt"] += "á" * ((_MAX_INPUT - len(raw)) // 2)
    raw = json.dumps(value, ensure_ascii=False).encode()
    raw += b" " * (_MAX_INPUT + delta - len(raw))
    assert len(raw) == _MAX_INPUT + delta
    if delta:
        with pytest.raises(ValueError, match="input size"):
            _closed_request(raw)
    else:
        assert _closed_request(raw) == value


def test_parent_dialogue_byte_limit_and_chunk_boundaries_remain(child):
    adapter, _, calls, _ = child()
    exchange = ConversationExchange("á" * 8192, ("b" * 8192, "c" * 8192))
    exact = replace(request(), recent_dialogue=(exchange, exchange))
    adapter.complete_audited(exact)
    assert json.loads(calls[0])["messages"][1][1] == "\n\n".join(
        exchange.assistant_reply_chunks
    )
    with pytest.raises(InvalidModelProposal, match="aggregate byte"):
        replace(
            exact,
            recent_dialogue=(*exact.recent_dialogue, ConversationExchange("a", ("b",))),
        )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{",
        b"{}",
        b'{"system_prompt":"s","system_prompt":"duplicate","messages":[["user","x"]]}',
        b'{"system_prompt":"s","messages":[]}',
        b'{"system_prompt":"s","messages":[["assistant","x"]]}',
        b'{"system_prompt":"s","messages":[["user","a"],["user","b"],["user","c"]]}',
        b'{"system_prompt":"s","messages":[["user","a"],["assistant",""] ,["user","c"]]}',
        b'{"system_prompt":"s","messages":[["user",""]]}',
        b'{"system_prompt":"s","messages":[["user",17]]}',
        b'{"system_prompt":"s","messages":[["user","x","extra"]]}',
    ],
)
def test_actual_cli_classifies_invalid_input_before_agent(child, raw):
    _, command, _, capture = child()
    result = subprocess.run(
        command, input=raw, capture_output=True, env={}, check=False
    )
    assert result.returncode == 64
    assert result.stdout == b""
    assert result.stderr == b"v2_hermes_child_failed:ChildInputRejected\n"
    assert not capture.exists()


@pytest.mark.parametrize(
    "mode, expected, attempts",
    [
        ("input", "ChildInputRejected", 1),
        ("execution", "ChildExecutionFailed", 1),
        ("output", "InvalidModelProposal", 2),
        ("surrogate", "InvalidModelProposal", 2),
    ],
)
def test_adapter_distinguishes_input_execution_and_model_output(
    child, monkeypatch, mode, expected, attempts
):
    adapter, _, calls, _ = child(mode)
    if mode == "input":
        monkeypatch.setattr("v2_adapters.hermes_model._request_wire", lambda *_: b"{}")
    with pytest.raises(Exception) as caught:
        adapter.complete_audited(request())
    assert type(caught.value).__name__ == expected
    assert len(calls) == attempts
    if mode not in {"output", "surrogate"}:
        assert not isinstance(caught.value, InvalidModelProposal)


@pytest.mark.parametrize(
    "failure", [OSError("no executable"), subprocess.TimeoutExpired("child", 1)]
)
def test_transport_failure_is_not_a_model_proposal(failure):
    calls = []

    def execute(*args, **kwargs):
        calls.append(kwargs)
        raise failure

    adapter = HermesModelAdapter(
        command=("child",),
        system_prompt="V8",
        timeout=1,
        transcript_key=b"t" * 32,
        environ={},
        run=execute,
    )
    with pytest.raises(Exception) as caught:
        adapter.complete_audited(request())
    assert type(caught.value).__name__ == "ChildExecutionFailed"
    assert len(calls) == 1


@pytest.mark.parametrize("raw", [b'{"x":NaN}', b'{"x":"\\ud800"}', b"\xff"])
def test_malformed_model_values_are_output_errors(raw):
    from v2_host.hermes_child import _extract_json

    with pytest.raises(InvalidModelProposal):
        _extract_json(raw)


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("input", "ChildInputRejected"),
        ("execution", "ChildExecutionFailed"),
        ("output", "InvalidModelProposal"),
    ],
)
def test_inbox_persists_distinct_failure_after_actual_child(
    child, tmp_path, monkeypatch, mode, expected
):
    from datetime import timedelta
    import sqlite3
    from tests.test_v2_inbox_relay_workers import NOW, _event
    from v2_application.inbox import SQLiteInbox
    from v2_application.inbox_worker import InboxTurnWorker

    adapter, _, _, _ = child(mode)
    if mode == "input":
        monkeypatch.setattr("v2_adapters.hermes_model._request_wire", lambda *_: b"{}")

    class Executor:
        def execute(self, batch):
            return adapter.complete_audited(request())

    path = tmp_path / "inbox.sqlite3"
    inbox = SQLiteInbox(path)
    inbox.accept(_event())
    worker = InboxTurnWorker(
        inbox=inbox,
        executor=Executor(),
        quiet_window=timedelta(0),
        lease_ttl=timedelta(minutes=1),
    )
    with pytest.raises(Exception) as caught:
        worker.run_once(now=NOW)
    assert type(caught.value).__name__ == expected
    reopened = SQLiteInbox(path)
    assert reopened.pending_count() == 1
    assert reopened.claimed_count() == 0
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT failure_reason,failure_count FROM inbound_events"
        ).fetchone() == (expected, 1)
