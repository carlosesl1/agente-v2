from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import reservation_boundary.sandbox as sandbox
from scripts import phase8_cloudbeds_read_child as cloudbeds_child
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
    def __init__(self, *responses: bytes, before_call=None) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.before_call = before_call

    def complete(
        self,
        *,
        system_prompt: str,
        messages: tuple[tuple[str, str], ...],
    ) -> bytes:
        if self.before_call is not None:
            self.before_call()
        self.calls.append((system_prompt, messages))
        return self.responses.pop(0)


class _QueueRead:
    def __init__(
        self,
        observation: sandbox.LodgingAvailabilityObservation,
        *,
        before_call=None,
    ) -> None:
        self.observation = observation
        self.calls: list[sandbox.LodgingAvailabilityReadRequest] = []
        self.before_call = before_call

    def read(
        self,
        request: sandbox.LodgingAvailabilityReadRequest,
    ) -> sandbox.LodgingAvailabilityObservation:
        if self.before_call is not None:
            self.before_call()
        self.calls.append(request)
        return self.observation


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
        hostile_price = json.loads(_observation())["options"][0]
        hostile_price["total_amount"] = "not-a-price"
        with self.assertRaises(sandbox.SandboxProtocolError):
            sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
                _observation(options=[hostile_price])
            )

    def test_two_call_read_loop_persists_only_final_public_turn(self) -> None:
        read_item: dict[str, object] = {
            "arguments": {
                "adults": 2,
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "children": 0,
            },
            "kind": "lodging_availability",
        }
        first = _response_with_reads([read_item])
        final = _response("Encontrei uma opção real para essas datas.")
        model = _QueueModel(first, final)
        observation = sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
            _observation()
        )
        reads = _QueueRead(observation)
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteSandboxStore(Path(tmp) / "sandbox.sqlite3")
            runner = SandboxConversation(
                store=store,
                model=model,
                reads=reads,
                knowledge={"brand": "Chapada Backpackers"},
            )

            result = runner.submit(
                session_id="lead-read",
                message="10 a 12 de agosto, para 2 adultos.",
            )

            self.assertEqual(result.reply, "Encontrei uma opção real para essas datas.")
            self.assertEqual(result.read_observation, observation)
            self.assertEqual(len(model.calls), 2)
            self.assertEqual(len(reads.calls), 1)
            follow_up = model.calls[1][1]
            self.assertEqual(follow_up[-2], ("assistant", first.decode("utf-8")))
            self.assertEqual(follow_up[-1][0], "user")
            self.assertTrue(follow_up[-1][1].startswith("READ_OBSERVATION="))
            self.assertIn(observation.canonical_hash(), follow_up[-1][1])
            self.assertEqual(store.load_messages("lead-read"), (
                ("user", "10 a 12 de agosto, para 2 adultos."),
                ("assistant", "Encontrei uma opção real para essas datas."),
            ))
            self.assertEqual(store.read_observation_count("lead-read"), 1)

    def test_model_and_read_calls_happen_without_sqlite_write_transaction(self) -> None:
        read_item: dict[str, object] = {
            "arguments": {
                "adults": 2,
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "children": 0,
            },
            "kind": "lodging_availability",
        }
        observation = sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
            _observation()
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sandbox.sqlite3"
            store = SQLiteSandboxStore(path)

            def assert_unlocked() -> None:
                connection = sqlite3.connect(path, timeout=0.1)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.rollback()
                finally:
                    connection.close()

            runner = SandboxConversation(
                store=store,
                model=_QueueModel(
                    _response_with_reads([read_item]),
                    _response("Consulta concluída."),
                    before_call=assert_unlocked,
                ),
                reads=_QueueRead(observation, before_call=assert_unlocked),
                knowledge={},
            )

            runner.submit(session_id="transaction-probe", message="Consulte.")

            self.assertEqual(store.read_observation_count("transaction-probe"), 1)

    def test_read_loop_fails_closed_without_port_or_on_second_read(self) -> None:
        read_item: dict[str, object] = {
            "arguments": {
                "adults": 2,
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "children": 0,
            },
            "kind": "lodging_availability",
        }
        observation = sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
            _observation()
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteSandboxStore(Path(tmp) / "sandbox.sqlite3")
            no_port = SandboxConversation(
                store=store,
                model=_QueueModel(_response_with_reads([read_item])),
                knowledge={},
            )
            with self.assertRaises(sandbox.ReadCallFailed):
                no_port.submit(session_id="no-port", message="Consulte.")
            self.assertEqual(store.load_messages("no-port"), ())

            repeated = SandboxConversation(
                store=store,
                model=_QueueModel(
                    _response_with_reads([read_item]),
                    _response_with_reads([read_item]),
                ),
                reads=_QueueRead(observation),
                knowledge={},
            )
            with self.assertRaises(sandbox.SandboxProtocolError):
                repeated.submit(session_id="second-read", message="Consulte.")
            self.assertEqual(store.load_messages("second-read"), ())

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

    def test_cloudbeds_child_strips_internal_fields_and_caps_options(self) -> None:
        request: dict[str, object] = {
            "adults": 2,
            "check_in": "2026-08-10",
            "check_out": "2026-08-12",
            "children": 0,
        }
        provider_options = [
            {
                "available_units": index + 1,
                "currency": "BRL",
                "option_id": f"internal-option-{index}",
                "price_reliable": True,
                "rate_plan_id": f"internal-rate-{index}",
                "room_public_name": f"Quarto {index}",
                "room_type_id": f"internal-room-{index}",
                "total_amount": f"{480 + index}.00",
            }
            for index in range(6)
        ]
        raw = _canonical(
            {
                "options": provider_options,
                "raw_provider_payload_returned": False,
                "status": "ok",
            }
        ).decode("utf-8")

        result = cloudbeds_child._sanitize_result(raw, request=request)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["options"]), 5)
        serialized = _canonical(result).decode("utf-8")
        self.assertNotIn("option_id", serialized)
        self.assertNotIn("room_type_id", serialized)
        self.assertNotIn("rate_plan_id", serialized)
        sandbox.LodgingAvailabilityObservation.from_canonical_bytes(_canonical(result))

    def test_cloudbeds_child_converts_untrusted_provider_shape_to_safe_error(self) -> None:
        result = cloudbeds_child._sanitize_result(
            '{"raw_provider_payload_returned":true,"status":"ok"}',
            request={
                "adults": 2,
                "check_in": "2026-08-10",
                "check_out": "2026-08-12",
                "children": 0,
            },
        )

        self.assertEqual(result["status"], "provider_error")
        self.assertEqual(result["options"], [])
        self.assertFalse(result["availability_confirmed"])
        self.assertFalse(result["price_confirmed"])
        self.assertFalse(result["raw_provider_payload_returned"])

    def test_cloudbeds_adapter_uses_allowlisted_no_shell_child(self) -> None:
        calls: list[tuple[tuple[str, ...], bytes, int]] = []

        def fake_run(command: tuple[str, ...], *, input: bytes, timeout: int) -> _RunResult:
            calls.append((command, input, timeout))
            return _RunResult(0, b"PHASE8_CLOUDBEDS_RESULT\x00" + _observation())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "scripts" / "phase8_cloudbeds_read_child.py"
            child.parent.mkdir()
            child.write_text("# fixed child source\n", encoding="utf-8")
            adapter = sandbox.CloudbedsDockerRead(
                project_root=root,
                container="chapada-leads-hermes",
                run=fake_run,
            )
            request = sandbox.LodgingAvailabilityReadRequest.from_mapping(
                {
                    "arguments": {
                        "adults": 2,
                        "check_in": "2026-08-10",
                        "check_out": "2026-08-12",
                        "children": 0,
                    },
                    "kind": "lodging_availability",
                }
            )
            observation = adapter.read(request)

        self.assertEqual(observation.status, "ok")
        command, payload, timeout = calls[0]
        self.assertEqual(command[:3], ("docker", "exec", "-i"))
        self.assertEqual(
            command[-4:],
            (
                "chapada-leads-hermes",
                "/app/.venv/bin/python",
                "-c",
                "# fixed child source\n",
            ),
        )
        self.assertNotIn("sh", command)
        self.assertNotIn("bash", command)
        required_environment = {
            "HERMES_LEADS_MODE=shadow",
            "HERMES_LEADS_DRY_RUN=false",
            "HERMES_LEADS_ALLOW_LIVE_SENDS=false",
            "HERMES_CLOUDBEDS_READONLY_ENABLED=true",
            "HERMES_CLOUDBEDS_WRITE_ENABLED=false",
            "HERMES_CLOUDBEDS_UPSELL_WRITE_ENABLED=false",
            "HERMES_CLOUDBEDS_PAYMENT_CONFIRMATION_WRITE_ENABLED=false",
            "HERMES_CLOUDBEDS_STRIPE_PAYMENT_LINK_WRITE_ENABLED=false",
            "HERMES_BOKUN_CART_WRITE_ENABLED=false",
            "HERMES_BOKUN_RESERVATION_WRITE_ENABLED=false",
            "HERMES_BOKUN_PAYMENT_CONFIRMATION_WRITE_ENABLED=false",
            "HERMES_STRIPE_PAYMENT_LINK_WRITE_ENABLED=false",
            "HERMES_WISE_PAYMENT_MATCHER_SETTLEMENT_ENABLED=false",
            "HERMES_WISE_PAYMENT_VALIDATION_ENABLED=false",
            "HERMES_WISE_CLOUDBEDS_HOSTEL_PAYMENT_VALIDATION_WRITE_ENABLED=false",
            "HERMES_SIDE_EFFECT_LEDGER_ENABLED=false",
            "HERMES_PUBLIC_OUTBOX_AUTO_FLUSH_ENABLED=false",
            "HERMES_POST_PAYMENT_OUTBOX_WORKER_ENABLED=false",
            "MANYCHAT_API_KEY=",
            "SUPABASE_URL=",
            "SUPABASE_SERVICE_ROLE_KEY=",
            "REDIS_URL=",
            "BOKUN_ACCESS_KEY=",
            "BOKUN_SECRET_KEY=",
            "STRIPE_SECRET_KEY=",
            "WISE_API_TOKEN=",
        }
        self.assertTrue(required_environment <= set(command))
        self.assertEqual(payload, request.to_canonical_bytes())
        self.assertEqual(timeout, 30)

    def test_cloudbeds_adapter_fails_closed_on_child_error_or_bad_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "scripts" / "phase8_cloudbeds_read_child.py"
            child.parent.mkdir()
            child.write_text("# fixed child source\n", encoding="utf-8")
            request = sandbox.LodgingAvailabilityReadRequest.from_mapping(
                {
                    "arguments": {
                        "adults": 2,
                        "check_in": "2026-08-10",
                        "check_out": "2026-08-12",
                        "children": 0,
                    },
                    "kind": "lodging_availability",
                }
            )
            failing = sandbox.CloudbedsDockerRead(
                project_root=root,
                run=lambda *_args, **_kwargs: _RunResult(2, b"", b"safe failure"),
            )
            with self.assertRaisesRegex(sandbox.ReadCallFailed, "safe failure"):
                failing.read(request)
            unmarked = sandbox.CloudbedsDockerRead(
                project_root=root,
                run=lambda *_args, **_kwargs: _RunResult(0, _observation()),
            )
            with self.assertRaisesRegex(sandbox.ReadCallFailed, "result marker"):
                unmarked.read(request)
            mismatched_option = json.loads(_observation())["options"][0]
            mismatched_option["check_in"] = "2026-09-01"
            mismatched_option["check_out"] = "2026-09-03"
            mismatched = sandbox.CloudbedsDockerRead(
                project_root=root,
                run=lambda *_args, **_kwargs: _RunResult(
                    0,
                    b"PHASE8_CLOUDBEDS_RESULT\x00"
                    + _observation(options=[mismatched_option]),
                ),
            )
            with self.assertRaisesRegex(sandbox.ReadCallFailed, "request binding"):
                mismatched.read(request)

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

        default_model = HermesDockerModel(
            project_root=Path("/workspace/project"),
            run=fake_run,
        )
        default_model.complete(
            system_prompt="isolado",
            messages=(("user", "oi"),),
        )
        self.assertEqual(
            calls[1][0][-4:],
            ("--provider", "openai-codex", "--model", "gpt-5.6-luna"),
        )

        failing = HermesDockerModel(
            project_root=Path("/workspace/project"),
            run=lambda *_args, **_kwargs: _RunResult(1, b"", b"auth failed"),
        )
        with self.assertRaisesRegex(ModelCallFailed, "auth failed"):
            failing.complete(system_prompt="x", messages=(("user", "y"),))


if __name__ == "__main__":
    unittest.main()
