from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import reservation_boundary.sandbox as sandbox
from reservation_boundary.sandbox import (
    HermesDockerModel,
    ModelCallFailed,
    SandboxConversation,
    SandboxProtocolError,
    SQLiteSandboxStore,
)


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _response(
    reply: str = "Olá! Como posso ajudar?",
    *,
    effects: list[dict[str, object]] | None = None,
) -> bytes:
    return _canonical(
        {
            "effect_proposals": effects or [],
            "facts": [],
            "intent": "inform",
            "read_requests": [],
            "reply": reply,
            "reply_type": "answer",
            "route": "recepcionista",
            "schema": "phase8-sandbox-model-response-v1",
        }
    )


def _response_with_reads(
    reads: list[dict[str, object]],
    *,
    reply: str = "Vou consultar a hospedagem.",
) -> bytes:
    value = json.loads(_response(reply))
    value["read_requests"] = reads
    return _canonical(value)


def _observation(
    *,
    options: list[dict[str, object]] | None = None,
) -> bytes:
    return _canonical(
        {
            "availability_confirmed": True,
            "options": options
            or [
                {
                    "adults": 2,
                    "available_units": 1,
                    "check_in": "2026-08-10",
                    "check_out": "2026-08-12",
                    "children": 0,
                    "currency": "BRL",
                    "nights": 2,
                    "price_reliable": True,
                    "room_public_name": "Quarto privativo",
                    "total_amount": "480.00",
                }
            ],
            "price_confirmed": True,
            "public_summary": "Encontrei uma opção de hospedagem.",
            "raw_provider_payload_returned": False,
            "schema": "phase8-sandbox-lodging-observation-v1",
            "status": "ok",
        }
    )


class _QueueModel:
    def __init__(self, *responses: bytes) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def complete(
        self,
        *,
        system_prompt: str,
        messages: tuple[tuple[str, str], ...],
    ) -> bytes:
        self.calls.append((system_prompt, messages))
        return self.responses.pop(0)


class _RunResult:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FastTrackSandboxTests(unittest.TestCase):
    def test_lodging_read_request_is_closed_and_canonical(self) -> None:
        payload = _response_with_reads(
            [
                {
                    "arguments": {
                        "adults": 2,
                        "check_in": "2026-08-10",
                        "check_out": "2026-08-12",
                        "children": 0,
                    },
                    "kind": "lodging_availability",
                }
            ]
        )

        response = sandbox.SandboxModelResponse.from_canonical_bytes(payload)

        self.assertEqual(len(response.read_requests), 1)
        request = response.read_requests[0]
        self.assertEqual(request.check_in, "2026-08-10")
        self.assertEqual(request.check_out, "2026-08-12")
        self.assertEqual(request.adults, 2)
        self.assertEqual(request.children, 0)
        self.assertEqual(
            json.loads(request.to_canonical_bytes()),
            {
                "arguments": {
                    "adults": 2,
                    "check_in": "2026-08-10",
                    "check_out": "2026-08-12",
                    "children": 0,
                },
                "kind": "lodging_availability",
            },
        )

    def test_lodging_read_request_rejects_hostile_shapes(self) -> None:
        arguments: dict[str, object] = {
            "adults": 2,
            "check_in": "2026-08-10",
            "check_out": "2026-08-12",
            "children": 0,
        }
        valid: dict[str, object] = {
            "arguments": arguments,
            "kind": "lodging_availability",
        }
        hostile = (
            {**valid, "extra": "no"},
            {**valid, "arguments": {**arguments, "adults": True}},
            {**valid, "arguments": {**arguments, "check_in": "10/08/2026"}},
            {**valid, "arguments": {**arguments, "check_out": "2026-08-10"}},
        )
        for item in hostile:
            with self.subTest(item=item):
                with self.assertRaises(sandbox.SandboxProtocolError):
                    sandbox.SandboxModelResponse.from_canonical_bytes(
                        _response_with_reads([item])
                    )
        with self.assertRaises(sandbox.SandboxProtocolError):
            sandbox.SandboxModelResponse.from_canonical_bytes(
                _response_with_reads([valid, valid])
            )

    def test_lodging_observation_round_trips_and_rejects_internal_ids(self) -> None:
        observation = sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
            _observation()
        )

        self.assertEqual(observation.status, "ok")
        self.assertEqual(observation.to_canonical_bytes(), _observation())
        self.assertRegex(observation.canonical_hash(), r"^[0-9a-f]{64}$")
        hostile_option = json.loads(_observation())["options"][0]
        hostile_option["room_type_id"] = "internal-101"
        with self.assertRaises(sandbox.SandboxProtocolError):
            sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
                _observation(options=[hostile_option])
            )

    def test_multi_turn_history_is_durable_and_isolated_by_session(self) -> None:
        model = _QueueModel(_response("Primeiro."), _response("Segundo."))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sandbox.sqlite3"
            store = SQLiteSandboxStore(path)
            runner = SandboxConversation(
                store=store,
                model=model,
                knowledge={"mode": "controlled-fixture", "faq": "sandbox"},
            )

            first = runner.submit(session_id="lead-1", message="Oi")
            second = runner.submit(session_id="lead-1", message="Continuando")

            self.assertEqual(first.ordinal, 1)
            self.assertEqual(second.ordinal, 2)
            self.assertEqual(second.reply, "Segundo.")
            self.assertEqual(
                model.calls[1][1],
                (
                    ("user", "Oi"),
                    ("assistant", "Primeiro."),
                    ("user", "Continuando"),
                ),
            )
            self.assertIn('"mode":"controlled-fixture"', model.calls[0][0])

            reopened = SQLiteSandboxStore(path)
            self.assertEqual(
                reopened.load_messages("lead-1"),
                (
                    ("user", "Oi"),
                    ("assistant", "Primeiro."),
                    ("user", "Continuando"),
                    ("assistant", "Segundo."),
                ),
            )
            self.assertEqual(reopened.load_messages("other-lead"), ())

    def test_every_effect_proposal_is_only_persisted_as_blocked(self) -> None:
        effects = [
            {"arguments": {"chat_id": "real-chat", "text": "não enviar"}, "kind": "manychat_send"},
            {"arguments": {"amount": "100.00"}, "kind": "payment"},
            {"arguments": {"product_id": "canonical-1"}, "kind": "reservation"},
        ]
        model = _QueueModel(_response("Estou em sandbox; nada foi executado.", effects=effects))
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteSandboxStore(Path(tmp) / "sandbox.sqlite3")
            result = SandboxConversation(store=store, model=model, knowledge={}).submit(
                session_id="lead-effects",
                message="Reserve e cobre agora",
            )

            self.assertEqual(
                tuple(item.kind for item in result.blocked_effects),
                ("manychat_send", "payment", "reservation"),
            )
            self.assertTrue(
                all(item.reason == "sandbox_effects_disabled" for item in result.blocked_effects)
            )
            expected_hash = hashlib.sha256(
                _canonical(effects[0])
            ).hexdigest()
            self.assertEqual(result.blocked_effects[0].proposal_hash, expected_hash)
            self.assertEqual(store.blocked_effect_count("lead-effects"), 3)
            self.assertFalse(hasattr(SandboxConversation, "execute_effect"))

    def test_model_response_is_closed_canonical_json(self) -> None:
        invalid = (
            b'{"schema":"phase8-sandbox-model-response-v1",'
            b'"schema":"phase8-sandbox-model-response-v1",'
            b'"intent":"inform","route":"recepcionista",'
            b'"reply_type":"answer","reply":"x","facts":[],"effect_proposals":[]}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            runner = SandboxConversation(
                store=SQLiteSandboxStore(Path(tmp) / "sandbox.sqlite3"),
                model=_QueueModel(invalid),
                knowledge={},
            )
            with self.assertRaises(SandboxProtocolError):
                runner.submit(session_id="lead-1", message="Oi")
            self.assertEqual(runner.store.load_messages("lead-1"), ())

    def test_hermes_adapter_uses_stdin_no_shell_and_fails_closed(self) -> None:
        calls: list[tuple[tuple[str, ...], bytes, int]] = []

        def fake_run(command: tuple[str, ...], *, input: bytes, timeout: int) -> _RunResult:
            calls.append((command, input, timeout))
            return _RunResult(0, b'PHASE8_RESULT\x00' + _response("Modelo real."))

        model = HermesDockerModel(
            project_root=Path("/workspace/project"),
            container="hermes-webui",
            provider="copilot",
            model="gpt-5.4-mini",
            run=fake_run,
        )
        result = model.complete(
            system_prompt="isolado",
            messages=(("user", "oi"),),
        )

        self.assertEqual(result, _response("Modelo real."))
        command, payload, timeout = calls[0]
        self.assertEqual(
            command,
            (
                "docker",
                "exec",
                "-i",
                "hermes-webui",
                "/app/venv/bin/python",
                "/workspace/project/scripts/phase8_hermes_child.py",
                "--provider",
                "copilot",
                "--model",
                "gpt-5.4-mini",
            ),
        )
        self.assertEqual(timeout, 180)
        self.assertEqual(
            json.loads(payload),
            {"messages": [["user", "oi"]], "system_prompt": "isolado"},
        )

        failing = HermesDockerModel(
            project_root=Path("/workspace/project"),
            run=lambda *_args, **_kwargs: _RunResult(1, b"", b"auth failed"),
        )
        with self.assertRaisesRegex(ModelCallFailed, "auth failed"):
            failing.complete(system_prompt="x", messages=(("user", "y"),))


if __name__ == "__main__":
    unittest.main()
