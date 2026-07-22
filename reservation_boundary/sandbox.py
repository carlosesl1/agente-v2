"""Fast-track conversational sandbox with no effect execution capability.

This module intentionally owns only a private conversation journal.  A model may
*propose* effects, but no effect/provider/sender port is accepted or imported;
every proposal is reduced to a durable ``blocked`` record.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Callable, Final, Protocol


_RESPONSE_SCHEMA: Final = "phase8-sandbox-model-response-v1"
_SESSION_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_KIND_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_INTENTS: Final = frozenset(("inform", "select", "adjust", "confirm", "request_handoff"))
_ROUTES: Final = frozenset(("recepcionista", "hostel", "agencia", "fechamento", "handoff", "no_reply"))
_REPLY_TYPES: Final = frozenset(("ask_more", "qualify", "answer", "handoff", "no_reply"))
_FACT_NAMES: Final = frozenset(("language", "service", "start_date", "end_date", "adults", "children"))
_RESULT_MARKER: Final = b"PHASE8_RESULT\x00"


class SandboxProtocolError(ValueError):
    """The model crossed the closed sandbox response boundary."""


class ModelCallFailed(RuntimeError):
    """The isolated model process did not produce an authenticated result marker."""


class SandboxModelPort(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        messages: tuple[tuple[str, str], ...],
    ) -> bytes: ...


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SandboxProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SandboxProtocolError("value is not closed JSON") from exc


def _parse_json(payload: bytes) -> object:
    if type(payload) is not bytes or not payload:
        raise SandboxProtocolError("model response must be non-empty exact bytes")
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SandboxProtocolError(f"non-finite JSON value: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxProtocolError("model response must be UTF-8 JSON") from exc


def _text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise SandboxProtocolError(f"{name} must be an exact string")
    if value != value.strip():
        raise SandboxProtocolError(f"{name} has surrounding whitespace")
    if not allow_empty and not value:
        raise SandboxProtocolError(f"{name} must be non-empty")
    if any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise SandboxProtocolError(f"{name} contains a control character")
    return value


def _closed_json(value: object, name: str) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is list:
        return [_closed_json(item, name) for item in value]
    if type(value) is dict:
        result: dict[str, object] = {}
        for key, item in value.items():
            _text(key, f"{name}.key")
            result[key] = _closed_json(item, name)
        return result
    raise SandboxProtocolError(f"{name} contains an unsupported JSON value")


@dataclass(frozen=True, slots=True)
class EffectProposal:
    kind: str
    arguments: dict[str, object]

    def __post_init__(self) -> None:
        if type(self.kind) is not str or _KIND_RE.fullmatch(self.kind) is None:
            raise SandboxProtocolError("effect kind is outside the identifier grammar")
        if type(self.arguments) is not dict:
            raise SandboxProtocolError("effect arguments must be an object")
        _closed_json(self.arguments, "effect arguments")

    def to_canonical_bytes(self) -> bytes:
        return _canonical_json({"arguments": self.arguments, "kind": self.kind})


@dataclass(frozen=True, slots=True)
class BlockedEffect:
    kind: str
    proposal_hash: str
    reason: str = "sandbox_effects_disabled"


@dataclass(frozen=True, slots=True)
class SandboxModelResponse:
    intent: str
    route: str
    reply_type: str
    reply: str
    facts: tuple[tuple[str, object], ...]
    effect_proposals: tuple[EffectProposal, ...]

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "SandboxModelResponse":
        parsed = _parse_json(payload)
        expected = {
            "schema",
            "intent",
            "route",
            "reply_type",
            "reply",
            "facts",
            "effect_proposals",
        }
        if type(parsed) is not dict or set(parsed) != expected:
            raise SandboxProtocolError("model response fields mismatch")
        if parsed["schema"] != _RESPONSE_SCHEMA:
            raise SandboxProtocolError("model response schema mismatch")
        intent = _text(parsed["intent"], "intent")
        route = _text(parsed["route"], "route")
        reply_type = _text(parsed["reply_type"], "reply_type")
        if intent not in _INTENTS or route not in _ROUTES or reply_type not in _REPLY_TYPES:
            raise SandboxProtocolError("model response enum is outside the closed set")
        if (intent == "request_handoff") != (route == "handoff"):
            raise SandboxProtocolError("handoff intent and route diverge")
        if (route == "handoff") != (reply_type == "handoff"):
            raise SandboxProtocolError("handoff route and reply type diverge")
        if (route == "no_reply") != (reply_type == "no_reply"):
            raise SandboxProtocolError("no-reply route and reply type diverge")
        reply = _text(parsed["reply"], "reply", allow_empty=route == "no_reply")
        if route == "no_reply" and reply:
            raise SandboxProtocolError("no-reply response contains public text")

        raw_facts = parsed["facts"]
        if type(raw_facts) is not list:
            raise SandboxProtocolError("facts must be an array")
        facts: list[tuple[str, object]] = []
        seen_facts: set[str] = set()
        for item in raw_facts:
            if type(item) is not dict or set(item) != {"name", "value"}:
                raise SandboxProtocolError("fact fields mismatch")
            name = _text(item["name"], "fact name")
            if name not in _FACT_NAMES or name in seen_facts:
                raise SandboxProtocolError("fact name is unknown or duplicated")
            value = _closed_json(item["value"], "fact value")
            if type(value) not in (str, int):
                raise SandboxProtocolError("fact value must be a scalar string or integer")
            facts.append((name, value))
            seen_facts.add(name)

        raw_effects = parsed["effect_proposals"]
        if type(raw_effects) is not list:
            raise SandboxProtocolError("effect_proposals must be an array")
        effects: list[EffectProposal] = []
        for item in raw_effects:
            if type(item) is not dict or set(item) != {"kind", "arguments"}:
                raise SandboxProtocolError("effect proposal fields mismatch")
            effects.append(EffectProposal(item["kind"], item["arguments"]))

        if _canonical_json(parsed) != payload:
            raise SandboxProtocolError("model response must be canonical JSON")
        return cls(intent, route, reply_type, reply, tuple(facts), tuple(effects))


@dataclass(frozen=True, slots=True)
class SandboxTurnResult:
    ordinal: int
    reply: str
    intent: str
    route: str
    reply_type: str
    blocked_effects: tuple[BlockedEffect, ...]


class SQLiteSandboxStore:
    """Private journal; never points at or imports the operational runtime store."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS sandbox_sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) STRICT;
                CREATE TABLE IF NOT EXISTS sandbox_messages (
                    session_id TEXT NOT NULL REFERENCES sandbox_sessions(session_id),
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                    content TEXT NOT NULL,
                    response_json BLOB,
                    PRIMARY KEY (session_id, ordinal, role)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS sandbox_blocked_effects (
                    session_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    effect_ordinal INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    proposal_hash TEXT NOT NULL,
                    reason TEXT NOT NULL CHECK (reason = 'sandbox_effects_disabled'),
                    PRIMARY KEY (session_id, ordinal, effect_ordinal),
                    FOREIGN KEY (session_id) REFERENCES sandbox_sessions(session_id)
                ) STRICT;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _session_id(value: str) -> str:
        if type(value) is not str or _SESSION_RE.fullmatch(value) is None:
            raise ValueError("session_id is outside the closed identifier grammar")
        return value

    def load_messages(self, session_id: str) -> tuple[tuple[str, str], ...]:
        session = self._session_id(session_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content
                FROM sandbox_messages
                WHERE session_id = ?
                ORDER BY ordinal, CASE role WHEN 'user' THEN 0 ELSE 1 END
                """,
                (session,),
            ).fetchall()
        return tuple((str(role), str(content)) for role, content in rows)

    def append_turn(
        self,
        *,
        session_id: str,
        message: str,
        response_bytes: bytes,
        response: SandboxModelResponse,
        blocked: tuple[BlockedEffect, ...],
    ) -> int:
        session = self._session_id(session_id)
        user_message = _text(message, "message")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO sandbox_sessions(session_id) VALUES (?)",
                (session,),
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) FROM sandbox_messages WHERE session_id = ?",
                (session,),
            ).fetchone()
            ordinal = int(row[0]) + 1
            connection.execute(
                "INSERT INTO sandbox_messages(session_id,ordinal,role,content) VALUES (?,?,?,?)",
                (session, ordinal, "user", user_message),
            )
            connection.execute(
                """
                INSERT INTO sandbox_messages(session_id,ordinal,role,content,response_json)
                VALUES (?,?,?,?,?)
                """,
                (session, ordinal, "assistant", response.reply, response_bytes),
            )
            for effect_ordinal, item in enumerate(blocked):
                connection.execute(
                    """
                    INSERT INTO sandbox_blocked_effects(
                        session_id,ordinal,effect_ordinal,kind,proposal_hash,reason
                    ) VALUES (?,?,?,?,?,?)
                    """,
                    (
                        session,
                        ordinal,
                        effect_ordinal,
                        item.kind,
                        item.proposal_hash,
                        item.reason,
                    ),
                )
            connection.commit()
        return ordinal

    def blocked_effect_count(self, session_id: str) -> int:
        session = self._session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM sandbox_blocked_effects WHERE session_id = ?",
                (session,),
            ).fetchone()
        return int(row[0])


class SandboxConversation:
    """One safe vertical slice: model → closed response → private durable journal."""

    def __init__(
        self,
        *,
        store: SQLiteSandboxStore,
        model: SandboxModelPort,
        knowledge: dict[str, object],
    ) -> None:
        if type(store) is not SQLiteSandboxStore:
            raise TypeError("store must be exact SQLiteSandboxStore")
        if not hasattr(model, "complete"):
            raise TypeError("model must implement complete")
        if type(knowledge) is not dict:
            raise TypeError("knowledge must be an exact dict")
        self.store = store
        self._model = model
        self._knowledge = _closed_json(knowledge, "knowledge")

    def _system_prompt(self) -> str:
        knowledge = _canonical_json(self._knowledge).decode("utf-8")
        return (
            "Você é Maya, atendente de turismo da Chapada Diamantina em um sandbox sem "
            "efeitos externos. Responda no idioma do lead, com naturalidade e objetividade. "
            "Use somente fatos presentes em KNOWLEDGE; nunca invente preço, disponibilidade, "
            "link, produto ou confirmação. Produto deve usar apenas ID canônico fornecido. "
            "Se faltarem dados, faça uma pergunta curta. Toda intenção de enviar mensagem, "
            "reservar, cobrar, pagar, aprender ou gravar fora deste diário deve aparecer em "
            "effect_proposals e nunca ser descrita como executada. Não há ferramentas. "
            "Retorne somente um objeto JSON com estas sete chaves exatas: "
            "schema,intent,route,reply_type,reply,facts,effect_proposals. "
            "schema deve ser phase8-sandbox-model-response-v1; intent é inform|select|adjust|"
            "confirm|request_handoff; route é recepcionista|hostel|agencia|fechamento|handoff|"
            "no_reply; reply_type é ask_more|qualify|answer|handoff|no_reply. facts contém no "
            "máximo um item por nome, somente para language|service|start_date|end_date|adults|"
            "children, e apenas quando o lead fornecer o valor; cada item é {name,value}. "
            "effect_proposals é uma lista de objetos "
            "{kind,arguments}. Produza JSON canônico sem markdown. KNOWLEDGE=" + knowledge
        )

    def submit(self, *, session_id: str, message: str) -> SandboxTurnResult:
        user_message = _text(message, "message")
        history = self.store.load_messages(session_id)
        messages = (*history, ("user", user_message))
        # Deliberately outside every SQLite transaction: provider latency cannot hold locks.
        response_bytes = self._model.complete(
            system_prompt=self._system_prompt(),
            messages=messages,
        )
        response = SandboxModelResponse.from_canonical_bytes(response_bytes)
        blocked = tuple(
            BlockedEffect(
                item.kind,
                hashlib.sha256(item.to_canonical_bytes()).hexdigest(),
            )
            for item in response.effect_proposals
        )
        ordinal = self.store.append_turn(
            session_id=session_id,
            message=user_message,
            response_bytes=response_bytes,
            response=response,
            blocked=blocked,
        )
        return SandboxTurnResult(
            ordinal,
            response.reply,
            response.intent,
            response.route,
            response.reply_type,
            blocked,
        )


class _CompletedProcess(Protocol):
    returncode: int
    stdout: bytes
    stderr: bytes


def _run_command(
    command: tuple[str, ...],
    *,
    input: bytes,
    timeout: int,
) -> _CompletedProcess:
    return subprocess.run(
        command,
        input=input,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


class HermesDockerModel:
    """No-shell adapter to an isolated, tool-free AIAgent inside WebUI's venv."""

    def __init__(
        self,
        *,
        project_root: Path,
        container: str = "hermes-webui",
        provider: str = "openai-codex",
        model: str = "gpt-5.6-sol",
        timeout: int = 180,
        run: Callable[..., _CompletedProcess] = _run_command,
    ) -> None:
        if not isinstance(project_root, Path) or not project_root.is_absolute():
            raise ValueError("project_root must be an absolute pathlib.Path")
        self._project_root = project_root
        self._container = _text(container, "container")
        self._provider = _text(provider, "provider")
        self._model = _text(model, "model")
        if type(timeout) is not int or timeout < 1:
            raise ValueError("timeout must be a positive exact integer")
        self._timeout = timeout
        self._run = run

    def complete(
        self,
        *,
        system_prompt: str,
        messages: tuple[tuple[str, str], ...],
    ) -> bytes:
        payload = _canonical_json(
            {
                "messages": [list(item) for item in messages],
                "system_prompt": system_prompt,
            }
        )
        command = (
            "docker",
            "exec",
            "-i",
            self._container,
            "/app/venv/bin/python",
            str(self._project_root / "scripts" / "phase8_hermes_child.py"),
            "--provider",
            self._provider,
            "--model",
            self._model,
        )
        try:
            result = self._run(command, input=payload, timeout=self._timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelCallFailed("isolated Hermes model process failed") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise ModelCallFailed(detail or f"model process exited {result.returncode}")
        marker_at = result.stdout.rfind(_RESULT_MARKER)
        if marker_at < 0:
            raise ModelCallFailed("model process did not emit the Phase 8 result marker")
        response = result.stdout[marker_at + len(_RESULT_MARKER) :]
        # Authenticate shape/canonical bytes before giving them to the caller.
        SandboxModelResponse.from_canonical_bytes(response)
        return response


__all__ = (
    "BlockedEffect",
    "EffectProposal",
    "HermesDockerModel",
    "ModelCallFailed",
    "SandboxConversation",
    "SandboxModelPort",
    "SandboxModelResponse",
    "SandboxProtocolError",
    "SandboxTurnResult",
    "SQLiteSandboxStore",
)
