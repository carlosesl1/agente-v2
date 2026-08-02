"""Private durable owner for conversational reservation-profile fallbacks.

The exact values in this module never belong to the public conversation projection,
Maya artifacts, logs, or evidence.  Only ordered field-presence markers may leave
this owner toward model context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from v2_contracts.model import ModelFact


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_PRIVATE_FACT_ORDER = ("full_name", "email", "country_code")
_PRIVATE_FACTS = frozenset(_PRIVATE_FACT_ORDER)


class PrivateCustomerFactValidationError(ValueError):
    """A private fallback did not satisfy the closed canonical contract."""


class PrivateCustomerFactIdentityConflict(RuntimeError):
    """A durable source-turn identity was replayed with divergent material."""


def _private_text(value: object, field: str) -> str:
    if type(value) is not str:
        raise PrivateCustomerFactValidationError(f"private {field} is invalid")
    normalized = unicodedata.normalize("NFKC", value)
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise PrivateCustomerFactValidationError(f"private {field} is invalid")
    return normalized


def canonical_full_name(value: object) -> str:
    normalized = " ".join(_private_text(value, "full name").split())
    if (
        not normalized
        or len(normalized) > 200
        or len(tuple(part for part in normalized.split(" ") if part)) < 2
    ):
        raise PrivateCustomerFactValidationError("private full name is invalid")
    return normalized


def canonical_email(value: object) -> str:
    normalized = _private_text(value, "email").strip().lower()
    if (
        not normalized
        or len(normalized) > 254
        or normalized.count("@") != 1
        or any(char.isspace() for char in normalized)
    ):
        raise PrivateCustomerFactValidationError("private email is invalid")
    local, domain = normalized.split("@", 1)
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        raise PrivateCustomerFactValidationError("private email is invalid")
    return normalized


def canonical_country_code(value: object) -> str:
    normalized = _private_text(value, "country").strip().upper()
    if _COUNTRY_RE.fullmatch(normalized) is None:
        raise PrivateCustomerFactValidationError("private country is invalid")
    return normalized


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise PrivateCustomerFactValidationError(
            f"private customer {field} is not canonical"
        )
    return value


def _hash(value: object, field: str) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise PrivateCustomerFactValidationError(
            f"private customer {field} is not canonical"
        )
    return value


def _utc(value: object, field: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise PrivateCustomerFactValidationError(
            f"private customer {field} must be exact UTC"
        )
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _domain_hash(domain: bytes, payload: bytes) -> str:
    return hashlib.sha256(domain + b"\0" + payload).hexdigest()


def _canonical_fact_rows(
    facts: tuple[ModelFact, ...],
) -> tuple[tuple[str, str], ...]:
    if type(facts) is not tuple or not facts or any(
        type(item) is not ModelFact for item in facts
    ):
        raise PrivateCustomerFactValidationError(
            "private customer facts must be a non-empty exact tuple"
        )
    names = tuple(item.name for item in facts)
    if len(names) != len(set(names)) or any(name not in _PRIVATE_FACTS for name in names):
        raise PrivateCustomerFactValidationError(
            "private customer fact is outside the closed catalog"
        )
    canonicalizers = {
        "full_name": canonical_full_name,
        "email": canonical_email,
        "country_code": canonical_country_code,
    }
    values = {
        item.name: canonicalizers[item.name](item.value)
        for item in facts
    }
    return tuple((name, values[name]) for name in _PRIVATE_FACT_ORDER if name in values)


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerFactSnapshot:
    lead_id: str
    full_name: str | None
    email: str | None
    country_code: str | None
    content_hash: str
    source_turns: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _identifier(self.lead_id, "lead_id")
        _hash(self.content_hash, "content_hash")
        if self.full_name is not None and canonical_full_name(self.full_name) != self.full_name:
            raise PrivateCustomerFactValidationError("private full name is not canonical")
        if self.email is not None and canonical_email(self.email) != self.email:
            raise PrivateCustomerFactValidationError("private email is not canonical")
        if (
            self.country_code is not None
            and canonical_country_code(self.country_code) != self.country_code
        ):
            raise PrivateCustomerFactValidationError("private country is not canonical")
        if type(self.source_turns) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or item[0] not in _PRIVATE_FACTS
            or type(item[1]) is not str
            or _ID_RE.fullmatch(item[1]) is None
            for item in self.source_turns
        ):
            raise PrivateCustomerFactValidationError(
                "private customer source turns are invalid"
            )
        expected_names = tuple(
            name
            for name, value in (
                ("full_name", self.full_name),
                ("email", self.email),
                ("country_code", self.country_code),
            )
            if value is not None
        )
        if tuple(item[0] for item in self.source_turns) != expected_names:
            raise PrivateCustomerFactValidationError(
                "private customer source turns disagree with values"
            )
        expected_hash = _snapshot_hash(
            self.full_name,
            self.email,
            self.country_code,
        )
        if self.content_hash != expected_hash:
            raise PrivateCustomerFactValidationError(
                "private customer content hash disagrees with values"
            )

    @property
    def present_fact_names(self) -> tuple[str, ...]:
        return tuple(item[0] for item in self.source_turns)

    def source_turn_for(self, fact_name: str) -> str | None:
        if fact_name not in _PRIVATE_FACTS:
            raise PrivateCustomerFactValidationError(
                "private customer fact is outside the closed catalog"
            )
        return dict(self.source_turns).get(fact_name)

    def public_presence(self) -> tuple[str, ...]:
        return self.present_fact_names


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCustomerFactWriteResult:
    snapshot: PrivateCustomerFactSnapshot
    supplied_in_turn: tuple[str, ...]
    changed_in_turn: tuple[str, ...]
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.snapshot) is not PrivateCustomerFactSnapshot:
            raise TypeError("private customer write snapshot must be exact")
        for field, value in (
            ("supplied_in_turn", self.supplied_in_turn),
            ("changed_in_turn", self.changed_in_turn),
        ):
            if (
                type(value) is not tuple
                or any(item not in _PRIVATE_FACTS for item in value)
                or tuple(name for name in _PRIVATE_FACT_ORDER if name in value) != value
            ):
                raise PrivateCustomerFactValidationError(
                    f"private customer {field} is invalid"
                )
        if not set(self.changed_in_turn).issubset(self.supplied_in_turn):
            raise PrivateCustomerFactValidationError(
                "private customer changed fields exceed supplied fields"
            )
        if type(self.replayed) is not bool:
            raise TypeError("private customer replayed must be an exact bool")


def _snapshot_hash(
    full_name: str | None,
    email: str | None,
    country_code: str | None,
) -> str:
    return _domain_hash(
        b"v2-private-customer-fact-snapshot-v1",
        _canonical_json(
            {
                "country_code": country_code,
                "email": email,
                "full_name": full_name,
            }
        ),
    )


_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS private_customer_fact_turns (
    lead_id TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_event_hash TEXT NOT NULL,
    fact_names_json TEXT NOT NULL,
    private_content_hash TEXT NOT NULL,
    persisted_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, source_turn_id)
) STRICT;
CREATE TABLE IF NOT EXISTS private_customer_facts (
    lead_id TEXT NOT NULL,
    fact_name TEXT NOT NULL CHECK (fact_name IN ('full_name','email','country_code')),
    private_value TEXT NOT NULL,
    value_hash TEXT NOT NULL,
    source_turn_id TEXT NOT NULL,
    source_event_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    persisted_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, fact_name)
) STRICT;
"""


class SQLitePrivateCustomerFactStore:
    """Physically separate, private, idempotent owner for fallback customer facts."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("private customer store path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._closed = False
        try:
            self._connection = sqlite3.connect(path, isolation_level=None)
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.executescript(_SCHEMA)
        except sqlite3.DatabaseError:
            raise RuntimeError("private customer store initialization failed") from None

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("private customer store is closed")

    def load(self, lead_id: str) -> PrivateCustomerFactSnapshot:
        self._require_open()
        canonical_lead = _identifier(lead_id, "lead_id")
        try:
            rows = self._connection.execute(
                "SELECT fact_name,private_value,source_turn_id "
                "FROM private_customer_facts WHERE lead_id=? ORDER BY "
                "CASE fact_name WHEN 'full_name' THEN 1 WHEN 'email' THEN 2 ELSE 3 END",
                (canonical_lead,),
            ).fetchall()
        except sqlite3.DatabaseError:
            raise RuntimeError("private customer store read failed") from None
        values: dict[str, str] = {}
        sources: list[tuple[str, str]] = []
        for name, value, source_turn_id in rows:
            if name not in _PRIVATE_FACTS or name in values:
                raise RuntimeError("private customer store row identity is invalid")
            canonicalizers = {
                "full_name": canonical_full_name,
                "email": canonical_email,
                "country_code": canonical_country_code,
            }
            canonical = canonicalizers[name](value)
            if canonical != value:
                raise RuntimeError("private customer store row is noncanonical")
            _identifier(source_turn_id, "source_turn_id")
            values[name] = value
            sources.append((name, source_turn_id))
        return PrivateCustomerFactSnapshot(
            lead_id=canonical_lead,
            full_name=values.get("full_name"),
            email=values.get("email"),
            country_code=values.get("country_code"),
            content_hash=_snapshot_hash(
                values.get("full_name"),
                values.get("email"),
                values.get("country_code"),
            ),
            source_turns=tuple(sources),
        )

    def persist_turn(
        self,
        *,
        lead_id: str,
        source_turn_id: str,
        source_event_hash: str,
        facts: tuple[ModelFact, ...],
        persisted_at: datetime,
    ) -> PrivateCustomerFactWriteResult:
        self._require_open()
        canonical_lead = _identifier(lead_id, "lead_id")
        canonical_turn = _identifier(source_turn_id, "source_turn_id")
        canonical_event_hash = _hash(source_event_hash, "source_event_hash")
        instant = _utc(persisted_at, "persisted_at")
        rows = _canonical_fact_rows(facts)
        names = tuple(name for name, _ in rows)
        payload_hash = _domain_hash(
            b"v2-private-customer-fact-turn-v1",
            _canonical_json(
                {
                    "facts": [
                        {"name": name, "value": value} for name, value in rows
                    ],
                    "lead_id": canonical_lead,
                    "source_event_hash": canonical_event_hash,
                    "source_turn_id": canonical_turn,
                }
            ),
        )
        names_json = _canonical_json(list(names)).decode("utf-8")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing_turn = self._connection.execute(
                "SELECT source_event_hash,fact_names_json,private_content_hash "
                "FROM private_customer_fact_turns WHERE lead_id=? AND source_turn_id=?",
                (canonical_lead, canonical_turn),
            ).fetchone()
            if existing_turn is not None:
                if existing_turn != (canonical_event_hash, names_json, payload_hash):
                    raise PrivateCustomerFactIdentityConflict(
                        "private customer source turn identity conflicts"
                    )
                self._connection.execute("COMMIT")
                return PrivateCustomerFactWriteResult(
                    snapshot=self.load(canonical_lead),
                    supplied_in_turn=names,
                    changed_in_turn=(),
                    replayed=True,
                )

            changed: list[str] = []
            for name, value in rows:
                existing = self._connection.execute(
                    "SELECT private_value,revision FROM private_customer_facts "
                    "WHERE lead_id=? AND fact_name=?",
                    (canonical_lead, name),
                ).fetchone()
                if existing is not None and existing[0] == value:
                    continue
                revision = 1 if existing is None else existing[1] + 1
                value_hash = _domain_hash(
                    b"v2-private-customer-fact-value-v1",
                    value.encode("utf-8"),
                )
                self._connection.execute(
                    "INSERT INTO private_customer_facts "
                    "(lead_id,fact_name,private_value,value_hash,source_turn_id,"
                    "source_event_hash,revision,persisted_at) VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(lead_id,fact_name) DO UPDATE SET "
                    "private_value=excluded.private_value,value_hash=excluded.value_hash,"
                    "source_turn_id=excluded.source_turn_id,"
                    "source_event_hash=excluded.source_event_hash,"
                    "revision=excluded.revision,persisted_at=excluded.persisted_at",
                    (
                        canonical_lead,
                        name,
                        value,
                        value_hash,
                        canonical_turn,
                        canonical_event_hash,
                        revision,
                        instant.isoformat(),
                    ),
                )
                changed.append(name)
            self._connection.execute(
                "INSERT INTO private_customer_fact_turns "
                "(lead_id,source_turn_id,source_event_hash,fact_names_json,"
                "private_content_hash,persisted_at) VALUES (?,?,?,?,?,?)",
                (
                    canonical_lead,
                    canonical_turn,
                    canonical_event_hash,
                    names_json,
                    payload_hash,
                    instant.isoformat(),
                ),
            )
            self._connection.execute("COMMIT")
        except PrivateCustomerFactIdentityConflict:
            self._connection.execute("ROLLBACK")
            raise
        except sqlite3.DatabaseError:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise RuntimeError("private customer store write failed") from None
        return PrivateCustomerFactWriteResult(
            snapshot=self.load(canonical_lead),
            supplied_in_turn=names,
            changed_in_turn=tuple(changed),
            replayed=False,
        )

    def turn_supplied_fact_names(
        self,
        lead_id: str,
        source_turn_id: str,
    ) -> tuple[str, ...]:
        self._require_open()
        canonical_lead = _identifier(lead_id, "lead_id")
        canonical_turn = _identifier(source_turn_id, "source_turn_id")
        try:
            row = self._connection.execute(
                "SELECT fact_names_json FROM private_customer_fact_turns "
                "WHERE lead_id=? AND source_turn_id=?",
                (canonical_lead, canonical_turn),
            ).fetchone()
        except sqlite3.DatabaseError:
            raise RuntimeError("private customer store read failed") from None
        if row is None:
            return ()
        try:
            decoded = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            raise RuntimeError("private customer turn journal is invalid") from None
        if (
            type(decoded) is not list
            or any(type(item) is not str or item not in _PRIVATE_FACTS for item in decoded)
            or tuple(name for name in _PRIVATE_FACT_ORDER if name in decoded)
            != tuple(decoded)
        ):
            raise RuntimeError("private customer turn journal is invalid")
        return tuple(decoded)

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._closed = True


__all__ = [
    "PrivateCustomerFactIdentityConflict",
    "PrivateCustomerFactSnapshot",
    "PrivateCustomerFactValidationError",
    "PrivateCustomerFactWriteResult",
    "SQLitePrivateCustomerFactStore",
    "canonical_country_code",
    "canonical_email",
    "canonical_full_name",
]
