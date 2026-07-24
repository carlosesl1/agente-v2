from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import reservation_boundary.sandbox as sandbox
from scripts import phase8_cloudbeds_read_child as cloudbeds_child
from scripts import run_phase8_sandbox as source_runner
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


def _activity_observation(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "activity_date": "2026-08-05",
        "availability_confirmed": True,
        "currency": "BRL",
        "participants": 2,
        "price_confirmed": True,
        "product_public_name": "Buracão",
        "public_summary": "Encontrei disponibilidade para o passeio.",
        "raw_provider_payload_returned": False,
        "schema": "phase8-sandbox-activity-observation-v1",
        "status": "ok",
        "total_amount": "700.00",
    }
    value.update(overrides)
    return _canonical(value)


def _v2_read_result(observation: bytes, request_payload: bytes) -> bytes:
    return _canonical(
        {
            "observation": json.loads(observation),
            "request_hash": hashlib.sha256(
                b"phase8-v2-read-request-v1\x00" + request_payload
            ).hexdigest(),
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


class _RoutingRead:
    def __init__(self, observations: dict[str, object], *, before_call=None) -> None:
        self.observations = observations
        self.calls: list[object] = []
        self.before_call = before_call

    def read(self, request: object) -> object:
        if self.before_call is not None:
            self.before_call()
        self.calls.append(request)
        return self.observations[request.kind]


class _RunResult:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FastTrackSandboxTests(unittest.TestCase):
    def test_source_runner_defaults_to_v2_worker_and_private_tour_catalog(self) -> None:
        knowledge = source_runner._knowledge(None)

        self.assertEqual(
            source_runner.DEFAULT_V2_READ_WORKER,
            "agente-v2-digest-canary-169a67c-worker",
        )
        self.assertEqual(
            knowledge["tour_catalog"],
            [{"canonical_id": "product:buracao", "public_name": "Cachoeira do Buracão"}],
        )
        self.assertIn("hospedagem e passeio", knowledge["notice"])
        source = Path(source_runner.__file__).read_text(encoding="utf-8")
        self.assertIn("reads=V2ProviderDockerRead(", source)
        self.assertIn('parser.add_argument("--read-worker-container"', source)
        self.assertNotIn("CloudbedsDockerRead", source)

    def test_source_runner_prompt_allows_two_distinct_grounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = SandboxConversation(
                store=SQLiteSandboxStore(Path(tmp) / "sandbox.sqlite3"),
                model=_QueueModel(_response()),
                knowledge=source_runner._knowledge(None),
            )

            prompt = runner._system_prompt()

        self.assertIn("activity_availability", prompt)
        self.assertIn("product:buracao", prompt)
        self.assertIn("READ_OBSERVATIONS", prompt)
        self.assertIn("no máximo dois", prompt)
        self.assertIn("value deve ser string ou integer", prompt)

    def test_v2_provider_child_sanitizes_activity_and_closes_effect_gates(self) -> None:
        child = importlib.import_module("scripts.phase8_v2_provider_read_child")
        environment = {
            "V2_RUNTIME_MODE": "dark_read_only",
            "V2_ENABLE_CLOUDBEDS_WRITES": "false",
            "V2_ENABLE_BOKUN_WRITES": "false",
            "V2_ENABLE_STRIPE_LINKS": "false",
            "V2_ENABLE_MANYCHAT_DELIVERY": "false",
        }
        child._validate_environment(environment)
        for gate in (
            "V2_ENABLE_CLOUDBEDS_WRITES",
            "V2_ENABLE_BOKUN_WRITES",
            "V2_ENABLE_STRIPE_LINKS",
            "V2_ENABLE_MANYCHAT_DELIVERY",
        ):
            with self.subTest(gate=gate):
                with self.assertRaises(ValueError):
                    child._validate_environment({**environment, gate: "true"})
        with self.assertRaises(ValueError):
            child._validate_environment({**environment, "V2_RUNTIME_MODE": "shadow"})

        request = {
            "arguments": {
                "activity_date": "2026-08-05",
                "participants": 2,
                "product_id": "product:buracao",
            },
            "kind": "activity_availability",
        }
        sanitized = child._sanitize_result(
            {
                "available": True,
                "bokun_product_id": "913372",
                "currency": "BRL",
                "product_id": "product:buracao",
                "product_public_name": "Buracão",
                "total_amount": "700.00",
            },
            request=request,
        )
        serialized = _canonical(sanitized).decode("utf-8")
        self.assertNotIn("product:buracao", serialized)
        self.assertNotIn("913372", serialized)
        observation = sandbox.ActivityAvailabilityObservation.from_canonical_bytes(
            _canonical(sanitized)
        )
        self.assertEqual(observation.activity_date, "2026-08-05")
        self.assertEqual(observation.participants, 2)
        self.assertEqual(observation.product_public_name, "Buracão")

    def test_v2_provider_adapter_is_no_shell_and_request_bound(self) -> None:
        calls: list[tuple[tuple[str, ...], bytes, int]] = []

        def fake_run(command: tuple[str, ...], *, input: bytes, timeout: int) -> _RunResult:
            calls.append((command, input, timeout))
            return _RunResult(
                0,
                b"PHASE8_V2_READ_RESULT\x00"
                + _v2_read_result(_activity_observation(), input),
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "scripts" / "phase8_v2_provider_read_child.py"
            child.parent.mkdir()
            child.write_text("# fixed V2 read child\n", encoding="utf-8")
            adapter = sandbox.V2ProviderDockerRead(
                project_root=root,
                container="v2-read-worker",
                run=fake_run,
            )
            request = sandbox.ActivityAvailabilityReadRequest.from_mapping(
                {
                    "arguments": {
                        "activity_date": "2026-08-05",
                        "participants": 2,
                        "product_id": "product:buracao",
                    },
                    "kind": "activity_availability",
                }
            )

            observation = adapter.read(request)

        self.assertIsInstance(observation, sandbox.ActivityAvailabilityObservation)
        command, payload, timeout = calls[0]
        self.assertEqual(
            command,
            (
                "docker",
                "exec",
                "-i",
                "v2-read-worker",
                "/usr/local/bin/python",
                "-c",
                "# fixed V2 read child\n",
            ),
        )
        self.assertNotIn("sh", command)
        self.assertNotIn("bash", command)
        self.assertEqual(payload, request.to_canonical_bytes())
        self.assertEqual(timeout, 30)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "scripts" / "phase8_v2_provider_read_child.py"
            child.parent.mkdir()
            child.write_text("# fixed V2 read child\n", encoding="utf-8")
            bad_marker = sandbox.V2ProviderDockerRead(
                project_root=root,
                run=lambda *_args, **_kwargs: _RunResult(0, _activity_observation()),
            )
            with self.assertRaisesRegex(sandbox.ReadCallFailed, "result marker"):
                bad_marker.read(request)
            mismatch = sandbox.V2ProviderDockerRead(
                project_root=root,
                run=lambda _command, *, input, timeout: _RunResult(
                    0,
                    b"PHASE8_V2_READ_RESULT\x00"
                    + _v2_read_result(
                        _activity_observation(activity_date="2026-08-06"),
                        input,
                    ),
                ),
            )
            with self.assertRaisesRegex(sandbox.ReadCallFailed, "request binding"):
                mismatch.read(request)
            wrong_hash = sandbox.V2ProviderDockerRead(
                project_root=root,
                run=lambda _command, *, input, timeout: _RunResult(
                    0,
                    b"PHASE8_V2_READ_RESULT\x00"
                    + _canonical(
                        {
                            "observation": json.loads(_activity_observation()),
                            "request_hash": "0" * 64,
                        }
                    ),
                ),
            )
            with self.assertRaisesRegex(sandbox.ReadCallFailed, "request binding"):
                wrong_hash.read(request)

    def test_activity_contract_request_is_id_only_closed_and_canonical(self) -> None:
        request_mapping = {
            "arguments": {
                "activity_date": "2026-08-05",
                "participants": 2,
                "product_id": "product:buracao",
            },
            "kind": "activity_availability",
        }

        request = sandbox.ActivityAvailabilityReadRequest.from_mapping(request_mapping)

        self.assertEqual(request.product_id, "product:buracao")
        self.assertEqual(request.activity_date, "2026-08-05")
        self.assertEqual(request.participants, 2)
        self.assertEqual(request.to_canonical_bytes(), _canonical(request_mapping))
        hostile = (
            {**request_mapping, "tour_name": "Buracão"},
            {**request_mapping, "arguments": {**request_mapping["arguments"], "tour_name": "Buracão"}},
            {**request_mapping, "arguments": {**request_mapping["arguments"], "participants": True}},
            {**request_mapping, "arguments": {**request_mapping["arguments"], "activity_date": "05/08/2026"}},
            {**request_mapping, "arguments": {**request_mapping["arguments"], "product_id": "Buracão"}},
        )
        for item in hostile:
            with self.subTest(item=item):
                with self.assertRaises(SandboxProtocolError):
                    sandbox.ActivityAvailabilityReadRequest.from_mapping(item)

    def test_activity_contract_observation_strips_ids_and_closes_price(self) -> None:
        observation = sandbox.ActivityAvailabilityObservation.from_canonical_bytes(
            _activity_observation()
        )

        self.assertEqual(observation.product_public_name, "Buracão")
        self.assertTrue(observation.availability_confirmed)
        self.assertTrue(observation.price_confirmed)
        self.assertEqual(observation.total_amount, "700.00")
        self.assertEqual(observation.to_canonical_bytes(), _activity_observation())
        self.assertRegex(observation.canonical_hash(), r"^[0-9a-f]{64}$")
        for private_field in ("product_id", "bokun_product_id", "availability_id", "raw_payload"):
            value = json.loads(_activity_observation())
            value[private_field] = "private"
            with self.subTest(private_field=private_field):
                with self.assertRaises(SandboxProtocolError):
                    sandbox.ActivityAvailabilityObservation.from_canonical_bytes(
                        _canonical(value)
                    )
        with self.assertRaises(SandboxProtocolError):
            sandbox.ActivityAvailabilityObservation.from_canonical_bytes(
                _activity_observation(total_amount="700", price_confirmed=True)
            )

    def test_activity_contract_model_response_allows_one_of_each_read_kind(self) -> None:
        lodging = {
            "arguments": {
                "adults": 2,
                "check_in": "2026-08-05",
                "check_out": "2026-08-06",
                "children": 0,
            },
            "kind": "lodging_availability",
        }
        activity = {
            "arguments": {
                "activity_date": "2026-08-05",
                "participants": 2,
                "product_id": "product:buracao",
            },
            "kind": "activity_availability",
        }

        response = sandbox.SandboxModelResponse.from_canonical_bytes(
            _response_with_reads([lodging, activity])
        )

        self.assertEqual(
            tuple(type(item) for item in response.read_requests),
            (sandbox.LodgingAvailabilityReadRequest, sandbox.ActivityAvailabilityReadRequest),
        )
        for invalid in ([activity, activity], [lodging, lodging], [lodging, activity, lodging]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(SandboxProtocolError):
                    sandbox.SandboxModelResponse.from_canonical_bytes(
                        _response_with_reads(invalid)
                    )

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
            self.assertTrue(follow_up[-1][1].startswith("READ_OBSERVATIONS="))
            self.assertIn(observation.canonical_hash(), follow_up[-1][1])
            self.assertEqual(store.load_messages("lead-read"), (
                ("user", "10 a 12 de agosto, para 2 adultos."),
                ("assistant", "Encontrei uma opção real para essas datas."),
            ))
            self.assertEqual(store.read_observation_count("lead-read"), 1)

    def test_hybrid_read_loop_journals_two_observations_atomically(self) -> None:
        lodging_request = {
            "arguments": {
                "adults": 2,
                "check_in": "2026-08-05",
                "check_out": "2026-08-06",
                "children": 0,
            },
            "kind": "lodging_availability",
        }
        activity_request = {
            "arguments": {
                "activity_date": "2026-08-05",
                "participants": 2,
                "product_id": "product:buracao",
            },
            "kind": "activity_availability",
        }
        first = _response_with_reads([lodging_request, activity_request])
        final = _response("Há hospedagem e Buracão disponíveis para vocês.")
        lodging = sandbox.LodgingAvailabilityObservation.from_canonical_bytes(
            _observation(
                options=[
                    {
                        "adults": 2,
                        "available_units": 2,
                        "check_in": "2026-08-05",
                        "check_out": "2026-08-06",
                        "children": 0,
                        "currency": "BRL",
                        "nights": 1,
                        "price_reliable": True,
                        "room_public_name": "Suíte Serra",
                        "total_amount": "150.00",
                    }
                ]
            )
        )
        activity = sandbox.ActivityAvailabilityObservation.from_canonical_bytes(
            _activity_observation()
        )
        model = _QueueModel(first, final)
        reads = _RoutingRead(
            {
                "lodging_availability": lodging,
                "activity_availability": activity,
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteSandboxStore(Path(tmp) / "sandbox.sqlite3")
            runner = SandboxConversation(
                store=store,
                model=model,
                reads=reads,
                knowledge={"catalog": {"Buracão": "product:buracao"}},
            )

            result = runner.submit(
                session_id="lead-hybrid",
                message="Hospedagem 5 a 6 e Buracão dia 5, para 2 adultos.",
            )

            self.assertEqual(tuple(item.kind for item in reads.calls), (
                "lodging_availability",
                "activity_availability",
            ))
            self.assertEqual(result.read_observations, (lodging, activity))
            self.assertIsNone(result.read_observation)
            self.assertEqual(store.read_observation_count("lead-hybrid"), 2)
            private = model.calls[1][1][-1]
            self.assertEqual(private[0], "user")
            self.assertTrue(private[1].startswith("READ_OBSERVATIONS="))
            self.assertIn(lodging.canonical_hash(), private[1])
            self.assertIn(activity.canonical_hash(), private[1])
            self.assertEqual(
                store.load_messages("lead-hybrid"),
                (
                    ("user", "Hospedagem 5 a 6 e Buracão dia 5, para 2 adultos."),
                    ("assistant", "Há hospedagem e Buracão disponíveis para vocês."),
                ),
            )

    def test_observation_migration_preserves_old_row_and_adds_kind_to_key(self) -> None:
        old_observation = _observation()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sandbox.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE sandbox_sessions (
                        session_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ) STRICT;
                    CREATE TABLE sandbox_read_observations (
                        session_id TEXT NOT NULL,
                        ordinal INTEGER NOT NULL,
                        kind TEXT NOT NULL CHECK (kind = 'lodging_availability'),
                        observation_json BLOB NOT NULL,
                        observation_hash TEXT NOT NULL,
                        PRIMARY KEY (session_id, ordinal),
                        FOREIGN KEY (session_id) REFERENCES sandbox_sessions(session_id)
                    ) STRICT;
                    INSERT INTO sandbox_sessions(session_id) VALUES ('legacy');
                    """
                )
                connection.execute(
                    """
                    INSERT INTO sandbox_read_observations(
                        session_id,ordinal,kind,observation_json,observation_hash
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        "legacy",
                        1,
                        "lodging_availability",
                        old_observation,
                        hashlib.sha256(old_observation).hexdigest(),
                    ),
                )

            SQLiteSandboxStore(path)

            with sqlite3.connect(path) as connection:
                pk = {
                    row[1]: row[5]
                    for row in connection.execute(
                        "PRAGMA table_info(sandbox_read_observations)"
                    )
                    if row[5]
                }
                rows = connection.execute(
                    "SELECT session_id,ordinal,kind FROM sandbox_read_observations"
                ).fetchall()
            self.assertEqual(pk, {"session_id": 1, "ordinal": 2, "kind": 3})
            self.assertEqual(rows, [("legacy", 1, "lodging_availability")])

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
