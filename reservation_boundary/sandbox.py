"""Fast-track conversational sandbox with no effect execution capability.

This module intentionally owns only a private conversation journal.  A model may
*propose* effects, but no effect/provider/sender port is accepted or imported;
every proposal is reduced to a durable ``blocked`` record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
_PRODUCT_ID_RE: Final = re.compile(r"^product:[a-z0-9][a-z0-9._-]{0,127}$")
DEFAULT_SANDBOX_MODEL: Final = "gpt-5.6-luna"


class SandboxProtocolError(ValueError):
    """The model crossed the closed sandbox response boundary."""


class ModelCallFailed(RuntimeError):
    """The isolated model process did not produce an authenticated result marker."""


class ReadCallFailed(RuntimeError):
    """The allowlisted read process did not produce an authenticated observation."""


class SandboxModelPort(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        messages: tuple[tuple[str, str], ...],
    ) -> bytes: ...


class SandboxReadPort(Protocol):
    def read(
        self,
        request: "SandboxReadRequest",
    ) -> "SandboxReadObservation": ...


@dataclass(frozen=True, slots=True)
class LodgingAvailabilityReadRequest:
    check_in: str
    check_out: str
    adults: int
    children: int

    @classmethod
    def from_mapping(cls, value: object) -> "LodgingAvailabilityReadRequest":
        if type(value) is not dict or set(value) != {"kind", "arguments"}:
            raise SandboxProtocolError("lodging read request fields mismatch")
        if value["kind"] != "lodging_availability":
            raise SandboxProtocolError("lodging read request kind mismatch")
        arguments = value["arguments"]
        expected = {"check_in", "check_out", "adults", "children"}
        if type(arguments) is not dict or set(arguments) != expected:
            raise SandboxProtocolError("lodging read arguments mismatch")
        check_in = _iso_date(arguments["check_in"], "check_in")
        check_out = _iso_date(arguments["check_out"], "check_out")
        if date.fromisoformat(check_out) <= date.fromisoformat(check_in):
            raise SandboxProtocolError("check_out must be after check_in")
        adults = _bounded_int(arguments["adults"], "adults", minimum=1, maximum=20)
        children = _bounded_int(
            arguments["children"], "children", minimum=0, maximum=20
        )
        return cls(check_in, check_out, adults, children)

    def to_mapping(self) -> dict[str, object]:
        return {
            "arguments": {
                "adults": self.adults,
                "check_in": self.check_in,
                "check_out": self.check_out,
                "children": self.children,
            },
            "kind": "lodging_availability",
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_json(self.to_mapping())

    @property
    def kind(self) -> str:
        return "lodging_availability"


@dataclass(frozen=True, slots=True)
class ActivityAvailabilityReadRequest:
    product_id: str
    activity_date: str
    participants: int

    @classmethod
    def from_mapping(cls, value: object) -> "ActivityAvailabilityReadRequest":
        if type(value) is not dict or set(value) != {"kind", "arguments"}:
            raise SandboxProtocolError("activity read request fields mismatch")
        if value["kind"] != "activity_availability":
            raise SandboxProtocolError("activity read request kind mismatch")
        arguments = value["arguments"]
        expected = {"product_id", "activity_date", "participants"}
        if type(arguments) is not dict or set(arguments) != expected:
            raise SandboxProtocolError("activity read arguments mismatch")
        product_id = _text(arguments["product_id"], "product_id")
        if _PRODUCT_ID_RE.fullmatch(product_id) is None:
            raise SandboxProtocolError("product_id must be a canonical product ID")
        activity_date = _iso_date(arguments["activity_date"], "activity_date")
        participants = _bounded_int(
            arguments["participants"], "participants", minimum=1, maximum=20
        )
        return cls(product_id, activity_date, participants)

    def to_mapping(self) -> dict[str, object]:
        return {
            "arguments": {
                "activity_date": self.activity_date,
                "participants": self.participants,
                "product_id": self.product_id,
            },
            "kind": "activity_availability",
        }

    def to_canonical_bytes(self) -> bytes:
        return _canonical_json(self.to_mapping())

    @property
    def kind(self) -> str:
        return "activity_availability"


SandboxReadRequest = LodgingAvailabilityReadRequest | ActivityAvailabilityReadRequest


_OBSERVATION_SCHEMA: Final = "phase8-sandbox-lodging-observation-v1"
_OBSERVATION_STATUSES: Final = frozenset(
    ("ok", "no_bookable_options", "provider_error")
)
_PUBLIC_OPTION_REQUIRED: Final = frozenset(
    (
        "adults",
        "check_in",
        "check_out",
        "children",
        "nights",
        "price_reliable",
        "room_public_name",
    )
)
_PUBLIC_OPTION_ALLOWED: Final = _PUBLIC_OPTION_REQUIRED | frozenset(
    ("available_units", "currency", "total_amount")
)


@dataclass(frozen=True, slots=True)
class LodgingAvailabilityObservation:
    status: str
    availability_confirmed: bool
    price_confirmed: bool
    options: tuple[dict[str, object], ...]
    public_summary: str
    raw_provider_payload_returned: bool = False

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "LodgingAvailabilityObservation":
        parsed = _parse_json(payload)
        expected = {
            "availability_confirmed",
            "options",
            "price_confirmed",
            "public_summary",
            "raw_provider_payload_returned",
            "schema",
            "status",
        }
        if type(parsed) is not dict or set(parsed) != expected:
            raise SandboxProtocolError("lodging observation fields mismatch")
        if parsed["schema"] != _OBSERVATION_SCHEMA:
            raise SandboxProtocolError("lodging observation schema mismatch")
        status = _text(parsed["status"], "observation status")
        if status not in _OBSERVATION_STATUSES:
            raise SandboxProtocolError("lodging observation status is outside the closed set")
        availability = parsed["availability_confirmed"]
        price = parsed["price_confirmed"]
        raw_returned = parsed["raw_provider_payload_returned"]
        if type(availability) is not bool or type(price) is not bool:
            raise SandboxProtocolError("lodging observation flags must be exact booleans")
        if raw_returned is not False:
            raise SandboxProtocolError("raw provider payload must remain false")
        raw_options = parsed["options"]
        if type(raw_options) is not list or len(raw_options) > 5:
            raise SandboxProtocolError("lodging observation options are outside the limit")
        options = tuple(_public_lodging_option(item) for item in raw_options)
        if status == "ok" and (not options or not availability):
            raise SandboxProtocolError("ok lodging observation requires available options")
        if status != "ok" and (options or availability or price):
            raise SandboxProtocolError("non-ok lodging observation cannot claim options")
        has_price = any("total_amount" in item for item in options)
        if price is not has_price:
            raise SandboxProtocolError("price_confirmed diverges from public options")
        summary = _text(parsed["public_summary"], "public_summary")
        observation = cls(status, availability, price, options, summary, False)
        if observation.to_canonical_bytes() != payload:
            raise SandboxProtocolError("lodging observation must be canonical JSON")
        return observation

    def to_canonical_bytes(self) -> bytes:
        return _canonical_json(
            {
                "availability_confirmed": self.availability_confirmed,
                "options": list(self.options),
                "price_confirmed": self.price_confirmed,
                "public_summary": self.public_summary,
                "raw_provider_payload_returned": False,
                "schema": _OBSERVATION_SCHEMA,
                "status": self.status,
            }
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            b"phase8-sandbox-lodging-observation-v1\x00"
            + self.to_canonical_bytes()
        ).hexdigest()

    @property
    def kind(self) -> str:
        return "lodging_availability"


_ACTIVITY_OBSERVATION_SCHEMA: Final = "phase8-sandbox-activity-observation-v1"


@dataclass(frozen=True, slots=True)
class ActivityAvailabilityObservation:
    status: str
    activity_date: str
    participants: int
    product_public_name: str
    availability_confirmed: bool
    price_confirmed: bool
    total_amount: str | None
    currency: str | None
    public_summary: str
    raw_provider_payload_returned: bool = False

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "ActivityAvailabilityObservation":
        parsed = _parse_json(payload)
        expected = {
            "activity_date",
            "availability_confirmed",
            "currency",
            "participants",
            "price_confirmed",
            "product_public_name",
            "public_summary",
            "raw_provider_payload_returned",
            "schema",
            "status",
            "total_amount",
        }
        if type(parsed) is not dict or set(parsed) != expected:
            raise SandboxProtocolError("activity observation fields mismatch")
        if parsed["schema"] != _ACTIVITY_OBSERVATION_SCHEMA:
            raise SandboxProtocolError("activity observation schema mismatch")
        status = _text(parsed["status"], "activity observation status")
        if status not in _OBSERVATION_STATUSES:
            raise SandboxProtocolError("activity observation status is outside the closed set")
        activity_date = _iso_date(parsed["activity_date"], "observation.activity_date")
        participants = _bounded_int(
            parsed["participants"], "observation.participants", minimum=1, maximum=20
        )
        public_name = _text(parsed["product_public_name"], "product_public_name")
        availability = parsed["availability_confirmed"]
        price = parsed["price_confirmed"]
        if type(availability) is not bool or type(price) is not bool:
            raise SandboxProtocolError("activity observation flags must be exact booleans")
        if parsed["raw_provider_payload_returned"] is not False:
            raise SandboxProtocolError("raw provider payload must remain false")
        amount = parsed["total_amount"]
        currency = parsed["currency"]
        if price:
            amount = _text(amount, "activity total_amount")
            if re.fullmatch(r"(?:0|[1-9][0-9]{0,8})\.[0-9]{2}", amount) is None:
                raise SandboxProtocolError("activity total_amount must be canonical money")
            currency = _text(currency, "activity currency")
            if re.fullmatch(r"[A-Z]{3}", currency) is None:
                raise SandboxProtocolError("activity currency must be uppercase ISO-like text")
        elif amount is not None or currency is not None:
            raise SandboxProtocolError("unconfirmed activity price must not expose money")
        if status == "ok" and not availability:
            raise SandboxProtocolError("ok activity observation requires availability")
        if status != "ok" and (availability or price):
            raise SandboxProtocolError("non-ok activity observation cannot claim availability")
        summary = _text(parsed["public_summary"], "public_summary")
        observation = cls(
            status,
            activity_date,
            participants,
            public_name,
            availability,
            price,
            amount,
            currency,
            summary,
            False,
        )
        if observation.to_canonical_bytes() != payload:
            raise SandboxProtocolError("activity observation must be canonical JSON")
        return observation

    def to_canonical_bytes(self) -> bytes:
        return _canonical_json(
            {
                "activity_date": self.activity_date,
                "availability_confirmed": self.availability_confirmed,
                "currency": self.currency,
                "participants": self.participants,
                "price_confirmed": self.price_confirmed,
                "product_public_name": self.product_public_name,
                "public_summary": self.public_summary,
                "raw_provider_payload_returned": False,
                "schema": _ACTIVITY_OBSERVATION_SCHEMA,
                "status": self.status,
                "total_amount": self.total_amount,
            }
        )

    def canonical_hash(self) -> str:
        return hashlib.sha256(
            b"phase8-sandbox-activity-observation-v1\x00"
            + self.to_canonical_bytes()
        ).hexdigest()

    @property
    def kind(self) -> str:
        return "activity_availability"


SandboxReadObservation = LodgingAvailabilityObservation | ActivityAvailabilityObservation


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


def _bounded_int(
    value: object,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        raise SandboxProtocolError(
            f"{name} must be an exact integer between {minimum} and {maximum}"
        )
    return value


def _iso_date(value: object, name: str) -> str:
    text = _text(value, name)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise SandboxProtocolError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != text:
        raise SandboxProtocolError(f"{name} must be a canonical ISO date")
    return text


def _public_lodging_option(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise SandboxProtocolError("lodging option must be an object")
    fields = set(value)
    if not _PUBLIC_OPTION_REQUIRED <= fields or not fields <= _PUBLIC_OPTION_ALLOWED:
        raise SandboxProtocolError("lodging option fields mismatch")
    result: dict[str, object] = {
        "adults": _bounded_int(value["adults"], "option.adults", minimum=1, maximum=20),
        "check_in": _iso_date(value["check_in"], "option.check_in"),
        "check_out": _iso_date(value["check_out"], "option.check_out"),
        "children": _bounded_int(
            value["children"], "option.children", minimum=0, maximum=20
        ),
        "nights": _bounded_int(value["nights"], "option.nights", minimum=1, maximum=365),
        "price_reliable": value["price_reliable"],
        "room_public_name": _text(value["room_public_name"], "option.room_public_name"),
    }
    if type(result["price_reliable"]) is not bool:
        raise SandboxProtocolError("option.price_reliable must be an exact boolean")
    if date.fromisoformat(str(result["check_out"])) <= date.fromisoformat(
        str(result["check_in"])
    ):
        raise SandboxProtocolError("option.check_out must be after check_in")
    expected_nights = (
        date.fromisoformat(str(result["check_out"]))
        - date.fromisoformat(str(result["check_in"]))
    ).days
    if result["nights"] != expected_nights:
        raise SandboxProtocolError("option.nights diverges from the stay interval")
    if "available_units" in value:
        result["available_units"] = _bounded_int(
            value["available_units"],
            "option.available_units",
            minimum=1,
            maximum=10_000,
        )
    if "total_amount" in value:
        amount = _text(value["total_amount"], "option.total_amount")
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,8})\.[0-9]{2}", amount) is None:
            raise SandboxProtocolError("option.total_amount must be canonical money")
        if result["price_reliable"] is not True:
            raise SandboxProtocolError("priced option must be reliable")
        result["total_amount"] = amount
        if "currency" not in value:
            raise SandboxProtocolError("priced option requires currency")
    elif "currency" in value:
        raise SandboxProtocolError("currency without a public price is forbidden")
    if "currency" in value:
        currency = _text(value["currency"], "option.currency")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise SandboxProtocolError("option.currency must be ISO-like uppercase")
        result["currency"] = currency
    return result


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
    read_requests: tuple[SandboxReadRequest, ...]
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
            "read_requests",
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

        raw_reads = parsed["read_requests"]
        if type(raw_reads) is not list or len(raw_reads) > 2:
            raise SandboxProtocolError("read_requests must contain at most two items")
        read_requests_list: list[SandboxReadRequest] = []
        seen_read_kinds: set[str] = set()
        for item in raw_reads:
            if type(item) is not dict:
                raise SandboxProtocolError("read request must be an object")
            kind = item.get("kind")
            if kind == "lodging_availability":
                request: SandboxReadRequest = LodgingAvailabilityReadRequest.from_mapping(item)
            elif kind == "activity_availability":
                request = ActivityAvailabilityReadRequest.from_mapping(item)
            else:
                raise SandboxProtocolError("read request kind is outside the closed set")
            if request.kind in seen_read_kinds:
                raise SandboxProtocolError("read request kind is duplicated")
            seen_read_kinds.add(request.kind)
            read_requests_list.append(request)
        read_requests = tuple(read_requests_list)

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
        return cls(
            intent,
            route,
            reply_type,
            reply,
            tuple(facts),
            read_requests,
            tuple(effects),
        )


@dataclass(frozen=True, slots=True)
class SandboxTurnResult:
    ordinal: int
    reply: str
    intent: str
    route: str
    reply_type: str
    blocked_effects: tuple[BlockedEffect, ...]
    read_observations: tuple[SandboxReadObservation, ...] = ()

    @property
    def read_observation(self) -> SandboxReadObservation | None:
        return self.read_observations[0] if len(self.read_observations) == 1 else None


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
                CREATE TABLE IF NOT EXISTS sandbox_read_observations (
                    session_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (
                        kind IN ('lodging_availability','activity_availability')
                    ),
                    observation_json BLOB NOT NULL,
                    observation_hash TEXT NOT NULL,
                    PRIMARY KEY (session_id, ordinal, kind),
                    FOREIGN KEY (session_id) REFERENCES sandbox_sessions(session_id)
                ) STRICT;
                """
            )
            self._migrate_read_observations(connection)

    @staticmethod
    def _migrate_read_observations(connection: sqlite3.Connection) -> None:
        primary_key = {
            str(row[1]): int(row[5])
            for row in connection.execute(
                "PRAGMA table_info(sandbox_read_observations)"
            )
            if row[5]
        }
        if primary_key == {"session_id": 1, "ordinal": 2, "kind": 3}:
            return
        if primary_key != {"session_id": 1, "ordinal": 2}:
            raise RuntimeError("sandbox read observation schema is not migratable")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "ALTER TABLE sandbox_read_observations RENAME TO sandbox_read_observations_legacy"
            )
            connection.execute(
                """
                CREATE TABLE sandbox_read_observations (
                    session_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK (
                        kind IN ('lodging_availability','activity_availability')
                    ),
                    observation_json BLOB NOT NULL,
                    observation_hash TEXT NOT NULL,
                    PRIMARY KEY (session_id, ordinal, kind),
                    FOREIGN KEY (session_id) REFERENCES sandbox_sessions(session_id)
                ) STRICT
                """
            )
            connection.execute(
                """
                INSERT INTO sandbox_read_observations(
                    session_id,ordinal,kind,observation_json,observation_hash
                )
                SELECT session_id,ordinal,kind,observation_json,observation_hash
                FROM sandbox_read_observations_legacy
                """
            )
            connection.execute("DROP TABLE sandbox_read_observations_legacy")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

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
        observations: tuple[SandboxReadObservation, ...] = (),
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
            for observation in observations:
                connection.execute(
                    """
                    INSERT INTO sandbox_read_observations(
                        session_id,ordinal,kind,observation_json,observation_hash
                    ) VALUES (?,?,?,?,?)
                    """,
                    (
                        session,
                        ordinal,
                        observation.kind,
                        observation.to_canonical_bytes(),
                        observation.canonical_hash(),
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

    def read_observation_count(self, session_id: str) -> int:
        session = self._session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM sandbox_read_observations WHERE session_id = ?",
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
        reads: SandboxReadPort | None = None,
        knowledge: dict[str, object],
    ) -> None:
        if type(store) is not SQLiteSandboxStore:
            raise TypeError("store must be exact SQLiteSandboxStore")
        if not hasattr(model, "complete"):
            raise TypeError("model must implement complete")
        if reads is not None and not hasattr(reads, "read"):
            raise TypeError("reads must implement read")
        if type(knowledge) is not dict:
            raise TypeError("knowledge must be an exact dict")
        self.store = store
        self._model = model
        self._reads = reads
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
            "effect_proposals e nunca ser descrita como executada. Você não possui ferramentas. "
            "Retorne somente um objeto JSON com estas oito chaves exatas: "
            "schema,intent,route,reply_type,reply,facts,read_requests,effect_proposals. "
            "schema deve ser phase8-sandbox-model-response-v1; intent é inform|select|adjust|"
            "confirm|request_handoff; route é recepcionista|hostel|agencia|fechamento|handoff|"
            "no_reply; reply_type é ask_more|qualify|answer|handoff|no_reply. facts contém no "
            "máximo um item por nome, somente para language|service|start_date|end_date|adults|"
            "children, e apenas quando o lead fornecer o valor; cada item é {name,value}, e "
            "value deve ser string ou integer, nunca boolean, lista, objeto ou null. "
            "read_requests deve ser [] ou conter no máximo dois objetos de kinds distintos: "
            "{kind:'lodging_availability',arguments:{check_in,check_out,adults,children}} "
            "e/ou {kind:'activity_availability',arguments:{product_id,activity_date,"
            "participants}}. Para passeio, use somente um product_id presente no catálogo "
            "privado de KNOWLEDGE, nunca nome ou ID fornecido pelo lead. Solicite cada leitura "
            "somente quando todos os seus argumentos estiverem presentes; ao solicitar qualquer "
            "leitura, effect_proposals deve ser []. Em pedido híbrido completo, solicite as duas "
            "leituras juntas. Se receber uma mensagem privada READ_OBSERVATIONS, use somente os "
            "dados públicos de todos os itens para a resposta final e retorne read_requests=[]. "
            "Nunca mostre IDs, hashes ou o envelope das observações ao lead. "
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
        observations: list[SandboxReadObservation] = []
        if response.read_requests:
            if response.effect_proposals:
                raise SandboxProtocolError(
                    "read request response cannot also propose effects"
                )
            if self._reads is None:
                raise ReadCallFailed("sandbox read port is unavailable")
            for request in response.read_requests:
                observation = self._reads.read(request)
                if (
                    type(request) is LodgingAvailabilityReadRequest
                    and type(observation) is not LodgingAvailabilityObservation
                ) or (
                    type(request) is ActivityAvailabilityReadRequest
                    and type(observation) is not ActivityAvailabilityObservation
                ):
                    raise ReadCallFailed("sandbox read port returned an invalid observation")
                observations.append(observation)
            private_observation = _canonical_json(
                {
                    "items": [
                        {
                            "hash": observation.canonical_hash(),
                            "kind": observation.kind,
                            "observation": _parse_json(
                                observation.to_canonical_bytes()
                            ),
                        }
                        for observation in observations
                    ],
                }
            ).decode("utf-8")
            final_messages = (
                *messages,
                ("assistant", response_bytes.decode("utf-8")),
                ("user", "READ_OBSERVATIONS=" + private_observation),
            )
            response_bytes = self._model.complete(
                system_prompt=self._system_prompt(),
                messages=final_messages,
            )
            response = SandboxModelResponse.from_canonical_bytes(response_bytes)
            if response.read_requests:
                raise SandboxProtocolError("second sandbox read request is forbidden")
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
            observations=tuple(observations),
        )
        return SandboxTurnResult(
            ordinal,
            response.reply,
            response.intent,
            response.route,
            response.reply_type,
            blocked,
            tuple(observations),
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


_CLOUDBEDS_RESULT_MARKER: Final = b"PHASE8_CLOUDBEDS_RESULT\x00"
_CLOUDBEDS_CHILD_ENVIRONMENT: Final = (
    "HERMES_LEADS_MODE=shadow",
    "HERMES_LEADS_DRY_RUN=false",
    "HERMES_LEADS_ALLOW_LIVE_SENDS=false",
    "HERMES_CLOUDBEDS_READONLY_ENABLED=true",
    "HERMES_CLOUDBEDS_WRITE_ENABLED=false",
    "HERMES_CLOUDBEDS_UPSELL_WRITE_ENABLED=false",
    "HERMES_CLOUDBEDS_PAYMENT_CONFIRMATION_WRITE_ENABLED=false",
    "HERMES_CLOUDBEDS_STRIPE_PAYMENT_LINK_WRITE_ENABLED=false",
    "HERMES_BOKUN_READONLY_ENABLED=false",
    "HERMES_BOKUN_CART_WRITE_ENABLED=false",
    "HERMES_BOKUN_RESERVATION_WRITE_ENABLED=false",
    "HERMES_BOKUN_PAYMENT_CONFIRMATION_WRITE_ENABLED=false",
    "HERMES_STRIPE_PAYMENT_LINK_WRITE_ENABLED=false",
    "HERMES_WISE_PAYMENT_MATCHER_ENABLED=false",
    "HERMES_WISE_PAYMENT_MATCHER_SETTLEMENT_ENABLED=false",
    "HERMES_WISE_PAYMENT_VALIDATION_ENABLED=false",
    "HERMES_WISE_CLOUDBEDS_HOSTEL_PAYMENT_VALIDATION_WRITE_ENABLED=false",
    "HERMES_SIDE_EFFECT_LEDGER_ENABLED=false",
    "HERMES_AUTO_FLUSH_ENABLED=false",
    "HERMES_PUBLIC_OUTBOX_AUTO_FLUSH_ENABLED=false",
    "HERMES_POST_PAYMENT_OUTBOX_WORKER_ENABLED=false",
    "MANYCHAT_API_KEY=",
    "MANYCHAT_WEBHOOK_SECRET=",
    "SUPABASE_URL=",
    "SUPABASE_SERVICE_ROLE_KEY=",
    "REDIS_URL=",
    "BOKUN_ACCESS_KEY=",
    "BOKUN_SECRET_KEY=",
    "STRIPE_SECRET_KEY=",
    "STRIPE_LIVE_SECRET_KEY=",
    "CLOUDBEDS_STRIPE_SECRET_KEY=",
    "CLOUDBEDS_STRIPE_LIVE_SECRET_KEY=",
    "WISE_API_TOKEN=",
    "WISE_CLOUDBEDS_HOSTEL_API_TOKEN=",
    "HERMES_LEADS_AGENT_EMAIL_PASSWORD_FILE=",
    "HERMES_LEADS_HANDOFF_NOTIFY_TO_EMAIL=",
)


class CloudbedsDockerRead:
    """Call one fixed Cloudbeds read in an ephemeral, effect-denied child."""

    def __init__(
        self,
        *,
        project_root: Path,
        container: str = "chapada-leads-hermes",
        timeout: int = 30,
        run: Callable[..., _CompletedProcess] = _run_command,
    ) -> None:
        if not isinstance(project_root, Path) or not project_root.is_absolute():
            raise TypeError("project_root must be an absolute pathlib.Path")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container) is None:
            raise ValueError("container name is invalid")
        if type(timeout) is not int or timeout <= 0:
            raise ValueError("timeout must be a positive exact integer")
        child_path = project_root / "scripts" / "phase8_cloudbeds_read_child.py"
        try:
            child_source = child_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReadCallFailed("Cloudbeds child source is unavailable") from exc
        if not child_source or len(child_source.encode("utf-8")) > 64 * 1024:
            raise ReadCallFailed("Cloudbeds child source is outside the size limit")
        self._container = container
        self._timeout = timeout
        self._run = run
        self._child_source = child_source

    def read(
        self,
        request: LodgingAvailabilityReadRequest,
    ) -> LodgingAvailabilityObservation:
        if type(request) is not LodgingAvailabilityReadRequest:
            raise TypeError("request must be an exact LodgingAvailabilityReadRequest")
        command: list[str] = ["docker", "exec", "-i"]
        for environment in _CLOUDBEDS_CHILD_ENVIRONMENT:
            command.extend(("-e", environment))
        command.extend(
            (
                self._container,
                "/app/.venv/bin/python",
                "-c",
                self._child_source,
            )
        )
        try:
            result = self._run(
                tuple(command),
                input=request.to_canonical_bytes(),
                timeout=self._timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReadCallFailed("Cloudbeds read child could not be executed") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise ReadCallFailed(detail or f"Cloudbeds child exited with {result.returncode}")
        marker_at = result.stdout.rfind(_CLOUDBEDS_RESULT_MARKER)
        if marker_at < 0:
            raise ReadCallFailed("Cloudbeds child result marker is missing")
        payload = result.stdout[marker_at + len(_CLOUDBEDS_RESULT_MARKER) :]
        try:
            observation = LodgingAvailabilityObservation.from_canonical_bytes(payload)
        except SandboxProtocolError as exc:
            raise ReadCallFailed("Cloudbeds child returned an invalid observation") from exc
        if any(
            option["check_in"] != request.check_in
            or option["check_out"] != request.check_out
            or option["adults"] != request.adults
            or option["children"] != request.children
            for option in observation.options
        ):
            raise ReadCallFailed("Cloudbeds observation failed request binding")
        return observation


_V2_READ_RESULT_MARKER: Final = b"PHASE8_V2_READ_RESULT\x00"


class V2ProviderDockerRead:
    """Execute one allowlisted read inside the effect-denied V2 worker."""

    def __init__(
        self,
        *,
        project_root: Path,
        container: str = "agente-v2-digest-canary-169a67c-worker",
        timeout: int = 30,
        run: Callable[..., _CompletedProcess] = _run_command,
    ) -> None:
        if not isinstance(project_root, Path) or not project_root.is_absolute():
            raise TypeError("project_root must be an absolute pathlib.Path")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container) is None:
            raise ValueError("container name is invalid")
        if type(timeout) is not int or timeout <= 0:
            raise ValueError("timeout must be a positive exact integer")
        child_path = project_root / "scripts" / "phase8_v2_provider_read_child.py"
        try:
            child_source = child_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReadCallFailed("V2 read child source is unavailable") from exc
        if not child_source or len(child_source.encode("utf-8")) > 64 * 1024:
            raise ReadCallFailed("V2 read child source is outside the size limit")
        self._container = container
        self._timeout = timeout
        self._run = run
        self._child_source = child_source

    @staticmethod
    def _request_hash(request: SandboxReadRequest) -> str:
        return hashlib.sha256(
            b"phase8-v2-read-request-v1\x00" + request.to_canonical_bytes()
        ).hexdigest()

    def read(self, request: SandboxReadRequest) -> SandboxReadObservation:
        if type(request) not in {
            LodgingAvailabilityReadRequest,
            ActivityAvailabilityReadRequest,
        }:
            raise TypeError("request must be an exact sandbox read request")
        command = (
            "docker",
            "exec",
            "-i",
            self._container,
            "/usr/local/bin/python",
            "-c",
            self._child_source,
        )
        try:
            result = self._run(
                command,
                input=request.to_canonical_bytes(),
                timeout=self._timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReadCallFailed("V2 read child could not be executed") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise ReadCallFailed(detail or f"V2 read child exited with {result.returncode}")
        marker_at = result.stdout.rfind(_V2_READ_RESULT_MARKER)
        if marker_at < 0:
            raise ReadCallFailed("V2 read child result marker is missing")
        payload = result.stdout[marker_at + len(_V2_READ_RESULT_MARKER) :]
        try:
            envelope = _parse_json(payload)
        except SandboxProtocolError as exc:
            raise ReadCallFailed("V2 read child returned an invalid envelope") from exc
        if type(envelope) is not dict or set(envelope) != {
            "observation",
            "request_hash",
        }:
            raise ReadCallFailed("V2 read child envelope fields mismatch")
        if envelope["request_hash"] != self._request_hash(request):
            raise ReadCallFailed("V2 read observation failed request binding")
        observation_payload = _canonical_json(envelope["observation"])
        try:
            if type(request) is LodgingAvailabilityReadRequest:
                observation: SandboxReadObservation = (
                    LodgingAvailabilityObservation.from_canonical_bytes(
                        observation_payload
                    )
                )
                if any(
                    option["check_in"] != request.check_in
                    or option["check_out"] != request.check_out
                    or option["adults"] != request.adults
                    or option["children"] != request.children
                    for option in observation.options
                ):
                    raise ReadCallFailed(
                        "V2 lodging observation failed request binding"
                    )
            else:
                observation = ActivityAvailabilityObservation.from_canonical_bytes(
                    observation_payload
                )
                if (
                    observation.activity_date != request.activity_date
                    or observation.participants != request.participants
                ):
                    raise ReadCallFailed(
                        "V2 activity observation failed request binding"
                    )
        except SandboxProtocolError as exc:
            raise ReadCallFailed("V2 read child returned an invalid observation") from exc
        return observation


class HermesDockerModel:
    """No-shell adapter to an isolated, tool-free AIAgent inside WebUI's venv."""

    def __init__(
        self,
        *,
        project_root: Path,
        container: str = "hermes-webui",
        provider: str = "openai-codex",
        model: str = DEFAULT_SANDBOX_MODEL,
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
    "ActivityAvailabilityObservation",
    "ActivityAvailabilityReadRequest",
    "BlockedEffect",
    "CloudbedsDockerRead",
    "DEFAULT_SANDBOX_MODEL",
    "EffectProposal",
    "HermesDockerModel",
    "LodgingAvailabilityObservation",
    "LodgingAvailabilityReadRequest",
    "ModelCallFailed",
    "ReadCallFailed",
    "SandboxConversation",
    "SandboxModelPort",
    "SandboxModelResponse",
    "SandboxProtocolError",
    "SandboxReadPort",
    "SandboxReadObservation",
    "SandboxReadRequest",
    "SandboxTurnResult",
    "SQLiteSandboxStore",
    "V2ProviderDockerRead",
)
