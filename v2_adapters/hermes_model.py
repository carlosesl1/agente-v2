"""Tool-free Hermes child-process adapter for Maya V2."""

from __future__ import annotations

from datetime import date
import json
import subprocess
from typing import Callable, Final

from v2_contracts.model import (
    EffectProposal,
    InvalidModelProposal,
    ModelFact,
    ModelProposal,
    ModelRequest,
)
from v2_contracts.providers import ReadKind, ReadRequest


_RESULT_MARKER: Final = b"PHASE8_RESULT\x00"
_RESPONSE_FIELDS: Final = frozenset(
    (
        "schema",
        "source_event_id",
        "intent",
        "reply_chunks",
        "facts",
        "read_requests",
        "effect_proposals",
        "target_offer_id",
        "confirmed_summary_version",
    )
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidModelProposal(f"duplicate model response key: {key}")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidModelProposal("model input is not closed JSON") from exc


def _read_wire(request: ReadRequest) -> dict[str, object]:
    return json.loads(request.to_canonical_bytes())


def _request_wire(request: ModelRequest, system_prompt: str) -> bytes:
    observations = [
        {
            "request_hash": item.request_hash,
            "provider": item.provider,
            "observed_at": item.observed_at.isoformat(),
            "expires_at": item.expires_at.isoformat(),
            "public_payload": item.public_payload,
        }
        for item in request.observations
    ]
    user_payload = {
        "request_id": request.request_id,
        "lead_id": request.lead_id,
        "source_event_id": request.source_event_id,
        "message": request.message,
        "locale": request.locale,
        "state_version": request.state_version,
        "observations": observations,
    }
    return _canonical(
        {
            "system_prompt": system_prompt,
            "messages": [["user", _canonical(user_payload).decode("utf-8")]],
        }
    )


def _fact(value: object) -> ModelFact:
    if type(value) is not dict or set(value) != {"name", "value"}:
        raise InvalidModelProposal("model fact fields mismatch")
    name = value["name"]
    fact_value = value["value"]
    if name in ("start_date", "end_date"):
        if type(fact_value) is not str:
            raise InvalidModelProposal("date fact must be an ISO string")
        try:
            fact_value = date.fromisoformat(fact_value)
        except ValueError as exc:
            raise InvalidModelProposal("date fact is invalid") from exc
    return ModelFact(name, fact_value)


def _read_request(value: object) -> ReadRequest:
    if type(value) is not dict:
        raise InvalidModelProposal("read request must be an exact object")
    fields = dict(value)
    try:
        fields["kind"] = ReadKind(fields["kind"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidModelProposal("read kind is invalid") from exc
    for field in ("check_in", "check_out", "activity_date"):
        raw = fields.get(field)
        if raw is not None:
            if type(raw) is not str:
                raise InvalidModelProposal(f"{field} must be an ISO date")
            try:
                fields[field] = date.fromisoformat(raw)
            except ValueError as exc:
                raise InvalidModelProposal(f"{field} is invalid") from exc
    try:
        return ReadRequest(**fields)
    except (TypeError, ValueError) as exc:
        raise InvalidModelProposal("read request is invalid") from exc


def _effect(value: object) -> EffectProposal:
    if type(value) is not dict or set(value) != {"kind", "arguments"}:
        raise InvalidModelProposal("effect proposal fields mismatch")
    return EffectProposal(value["kind"], value["arguments"])


def _tuple_items(value: object, name: str) -> tuple[object, ...]:
    if type(value) is not list:
        raise InvalidModelProposal(f"{name} must be an exact list")
    return tuple(value)


def _proposal(payload: bytes, source_event_id: str) -> ModelProposal:
    try:
        decoded = json.loads(payload, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise InvalidModelProposal("model response is not valid JSON") from exc
    if type(decoded) is not dict or set(decoded) != _RESPONSE_FIELDS:
        raise InvalidModelProposal("model response fields mismatch")
    if decoded["schema"] != "v2-model-proposal-v1":
        raise InvalidModelProposal("model response schema mismatch")
    if decoded["source_event_id"] != source_event_id:
        raise InvalidModelProposal("model response source event mismatch")
    try:
        return ModelProposal(
            source_event_id=decoded["source_event_id"],
            intent=decoded["intent"],
            reply_chunks=tuple(_tuple_items(decoded["reply_chunks"], "reply_chunks")),
            facts=tuple(_fact(item) for item in _tuple_items(decoded["facts"], "facts")),
            read_requests=tuple(
                _read_request(item)
                for item in _tuple_items(decoded["read_requests"], "read_requests")
            ),
            effect_proposals=tuple(
                _effect(item)
                for item in _tuple_items(decoded["effect_proposals"], "effect_proposals")
            ),
            target_offer_id=decoded["target_offer_id"],
            confirmed_summary_version=decoded["confirmed_summary_version"],
        )
    except (TypeError, ValueError) as exc:
        if type(exc) is InvalidModelProposal:
            raise
        raise InvalidModelProposal("model proposal is invalid") from exc


class HermesModelAdapter:
    def __init__(
        self,
        *,
        command: tuple[str, ...],
        system_prompt: str,
        timeout: int,
        run: Callable[..., object] = subprocess.run,
    ) -> None:
        if type(command) is not tuple or not command or any(
            type(item) is not str or not item or "\x00" in item for item in command
        ):
            raise ValueError("command must be a non-empty exact string tuple")
        if type(system_prompt) is not str or not system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty exact string")
        if type(timeout) is not int or timeout < 1:
            raise ValueError("timeout must be a positive exact integer")
        if not callable(run):
            raise TypeError("run must be callable")
        self._command = command
        self._system_prompt = system_prompt
        self._timeout = timeout
        self._run = run

    def complete(self, request: ModelRequest) -> ModelProposal:
        if type(request) is not ModelRequest:
            raise TypeError("request must be an exact ModelRequest")
        try:
            result = self._run(
                self._command,
                input=_request_wire(request, self._system_prompt),
                capture_output=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise InvalidModelProposal("Hermes child process failed") from exc
        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", None)
        stderr = getattr(result, "stderr", None)
        if type(returncode) is not int or type(stdout) is not bytes or type(stderr) is not bytes:
            raise InvalidModelProposal("Hermes child returned an invalid process result")
        if returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise InvalidModelProposal(detail or f"Hermes child exited {returncode}")
        marker_at = stdout.rfind(_RESULT_MARKER)
        if marker_at < 0:
            raise InvalidModelProposal("Hermes child result marker is missing")
        response = stdout[marker_at + len(_RESULT_MARKER) :]
        if not response or len(response) > 128 * 1024:
            raise InvalidModelProposal("Hermes model response size is invalid")
        return _proposal(response, request.source_event_id)


__all__ = ["HermesModelAdapter"]
