#!/usr/bin/env python3
"""Isolated Hermes child: no tools, no memory, no durable Hermes session."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile

from hermes_state import SessionDB
from run_agent import AIAgent


_RESULT_MARKER = b"PHASE8_RESULT\x00"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_input() -> tuple[str, list[dict[str, str]], str]:
    raw = sys.stdin.buffer.read()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    if type(value) is not dict or set(value) != {"messages", "system_prompt"}:
        raise ValueError("input fields mismatch")
    system_prompt = value["system_prompt"]
    messages = value["messages"]
    if type(system_prompt) is not str or not system_prompt:
        raise ValueError("system_prompt must be a non-empty string")
    if type(messages) is not list or not messages:
        raise ValueError("messages must be a non-empty array")
    history: list[dict[str, str]] = []
    expected = "user"
    for index, item in enumerate(messages):
        if type(item) is not list or len(item) != 2:
            raise ValueError("message must be [role, content]")
        role, content = item
        if role != expected or type(content) is not str or not content:
            raise ValueError("messages must alternate non-empty user/assistant content")
        if index < len(messages) - 1:
            history.append({"role": role, "content": content})
        expected = "assistant" if role == "user" else "user"
    if messages[-1][0] != "user":
        raise ValueError("last message must be user")
    return system_prompt, history, messages[-1][1]


def _canonical_model_json(text: object) -> bytes:
    if type(text) is not str:
        raise ValueError("Hermes final_response must be a string")
    value = json.loads(text, object_pairs_hook=_unique_object)
    if type(value) is not dict:
        raise ValueError("Hermes final_response must be a JSON object")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    try:
        system_prompt, history, user_message = _load_input()
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        with tempfile.TemporaryDirectory(prefix="phase8-hermes-child-") as tmp:
            with redirect_stdout(captured_out), redirect_stderr(captured_err):
                agent = AIAgent(
                    provider=args.provider,
                    model=args.model,
                    enabled_toolsets=[],
                    quiet_mode=True,
                    max_iterations=2,
                    skip_context_files=True,
                    load_soul_identity=False,
                    skip_memory=True,
                    session_db=SessionDB(Path(tmp) / "session.db"),
                    platform="tool",
                )
                # API failures must not create request dumps in the operational
                # Hermes home.  The child owns only this disposable directory.
                agent.logs_dir = Path(tmp) / "logs"
                agent.logs_dir.mkdir(mode=0o700)
                if agent.valid_tool_names or agent.tools:
                    raise RuntimeError("tool-free child unexpectedly loaded tools")
                result = agent.run_conversation(
                    user_message,
                    system_message=system_prompt,
                    conversation_history=history,
                )
        if result.get("error"):
            raise RuntimeError(str(result["error"]))
        payload = _canonical_model_json(result.get("final_response"))
        sys.stdout.buffer.write(_RESULT_MARKER + payload)
        sys.stdout.buffer.flush()
        return 0
    except Exception as exc:
        print(f"phase8 isolated model failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
