"""Tool-free wrapper that turns Hermes CLI output into the Phase 8 result wire."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import subprocess
import sys
from typing import Final

_RESULT_MARKER: Final = b"PHASE8_RESULT\x00"
_MAX_INPUT: Final = 512 * 1024
_MAX_OUTPUT: Final = 128 * 1024


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _closed_request(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > _MAX_INPUT:
        raise ValueError("model wire input size is invalid")
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if type(value) is not dict or set(value) != {"system_prompt", "messages"}:
        raise ValueError("model wire input fields mismatch")
    if type(value["system_prompt"]) is not str or not value["system_prompt"].strip():
        raise ValueError("model system prompt is invalid")
    messages = value["messages"]
    if (
        type(messages) is not list
        or len(messages) != 1
        or type(messages[0]) is not list
        or len(messages[0]) != 2
        or messages[0][0] != "user"
        or type(messages[0][1]) is not str
    ):
        raise ValueError("model messages wire is invalid")
    return value


def _prompt(request: dict[str, object]) -> str:
    return (
        request["system_prompt"]
        + "\n\nYou are running as a tool-free child. Do not call tools or perform effects. "
        "Return exactly one JSON object conforming to v2-model-proposal-v2, with no "
        "Markdown fence, preface, or trailing commentary. The parent validates every field.\n\n"
        + request["messages"][0][1]
    )


def _extract_json(raw: bytes) -> bytes:
    if not raw or len(raw) > _MAX_OUTPUT:
        raise ValueError("Hermes CLI output size is invalid")
    text = raw.decode("utf-8").strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    starts = [index for index, char in enumerate(text) if char == "{"]
    for start in starts:
        try:
            value, end = decoder.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            continue
        if text[end:].strip():
            continue
        if type(value) is not dict:
            continue
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    raise ValueError("Hermes CLI did not return one closed JSON object")


def run(
    argv: Sequence[str],
    stdin_bytes: bytes,
    *,
    execute: Callable[..., object] = subprocess.run,
) -> bytes:
    if not argv or any(type(item) is not str or not item for item in argv):
        raise ValueError("Hermes CLI command is required")
    request = _closed_request(stdin_bytes)
    result = execute(
        (*argv, "--toolsets", "", "-z", _prompt(request)),
        capture_output=True,
        timeout=120,
        check=False,
    )
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    if type(returncode) is not int or type(stdout) is not bytes or type(stderr) is not bytes:
        raise RuntimeError("Hermes CLI returned an invalid process result")
    if returncode != 0:
        raise RuntimeError(f"Hermes CLI exited {returncode}")
    return _RESULT_MARKER + _extract_json(stdout)


def main() -> None:
    try:
        output = run(tuple(sys.argv[1:]), sys.stdin.buffer.read(_MAX_INPUT + 1))
    except Exception as exc:
        # Keep stderr categorical; input, provider output and credentials are private.
        print(f"v2_hermes_child_failed:{type(exc).__name__}", file=sys.stderr)
        raise SystemExit(65) from exc
    sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()


__all__ = ["main", "run"]
