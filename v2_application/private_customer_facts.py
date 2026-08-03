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
_COUNTRY_RE = re.compile(r"[A-Z]{2}")
_ISO_ALPHA2_CODES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI "
    "BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN "
    "CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK "
    "FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM "
    "HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN "
    "KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK "
    "ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP "
    "NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW "
    "SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF "
    "TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI "
    "VN VU WF WS YE YT ZA ZM ZW".split()
)
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
    if (
        _COUNTRY_RE.fullmatch(normalized) is None
        or normalized not in _ISO_ALPHA2_CODES
    ):
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

_EXPECTED_SCHEMA = {
    "private_customer_fact_turns": (
        ("lead_id", "TEXT", 1, 1),
        ("source_turn_id", "TEXT", 1, 2),
        ("source_event_hash", "TEXT", 1, 0),
        ("fact_names_json", "TEXT", 1, 0),
        ("private_content_hash", "TEXT", 1, 0),
        ("persisted_at", "TEXT", 1, 0),
    ),
    "private_customer_facts": (
        ("lead_id", "TEXT", 1, 1),
        ("fact_name", "TEXT", 1, 2),
        ("private_value", "TEXT", 1, 0),
        ("value_hash", "TEXT", 1, 0),
        ("source_turn_id", "TEXT", 1, 0),
        ("source_event_hash", "TEXT", 1, 0),
        ("revision", "INTEGER", 1, 0),
        ("persisted_at", "TEXT", 1, 0),
    ),
}

_EXPECTED_TABLE_SQL = {
    "private_customer_fact_turns": """
        CREATE TABLE private_customer_fact_turns (
            lead_id TEXT NOT NULL,
            source_turn_id TEXT NOT NULL,
            source_event_hash TEXT NOT NULL,
            fact_names_json TEXT NOT NULL,
            private_content_hash TEXT NOT NULL,
            persisted_at TEXT NOT NULL,
            PRIMARY KEY (lead_id, source_turn_id)
        ) STRICT
    """,
    "private_customer_facts": """
        CREATE TABLE private_customer_facts (
            lead_id TEXT NOT NULL,
            fact_name TEXT NOT NULL
                CHECK (fact_name IN ('full_name','email','country_code')),
            private_value TEXT NOT NULL,
            value_hash TEXT NOT NULL,
            source_turn_id TEXT NOT NULL,
            source_event_hash TEXT NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            persisted_at TEXT NOT NULL,
            PRIMARY KEY (lead_id, fact_name)
        ) STRICT
    """,
}


def _normalized_schema_sql(value: object) -> str:
    if type(value) is not str:
        raise RuntimeError("private customer schema is incompatible")
    return " ".join(value.split())


def _validate_schema(connection: sqlite3.Connection) -> None:
    table_rows = {
        row[1]: row
        for row in connection.execute("PRAGMA table_list").fetchall()
        if row[1] in _EXPECTED_SCHEMA
    }
    if set(table_rows) != set(_EXPECTED_SCHEMA):
        raise RuntimeError("private customer schema is incompatible")
    for table, expected in _EXPECTED_SCHEMA.items():
        table_row = table_rows[table]
        if table_row[2] != "table" or table_row[5] != 1:
            raise RuntimeError("private customer schema is incompatible")
        actual = tuple(
            (row[1], row[2], row[3], row[5])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        )
        if actual != expected:
            raise RuntimeError("private customer schema is incompatible")
        schema_row = connection.execute(
            "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if (
            schema_row is None
            or _normalized_schema_sql(schema_row[0])
            != _normalized_schema_sql(_EXPECTED_TABLE_SQL[table])
        ):
            raise RuntimeError("private customer schema is incompatible")


def _value_hash(value: str) -> str:
    return _domain_hash(
        b"v2-private-customer-fact-value-v1",
        value.encode("utf-8"),
    )


def _journal_fact_material(value: object) -> tuple[tuple[str, str], ...]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError("private customer turn journal is invalid") from None
    if (
        type(decoded) is not list
        or any(
            type(item) is not dict
            or set(item) != {"name", "value_hash"}
            or item["name"] not in _PRIVATE_FACTS
            or type(item["value_hash"]) is not str
            or _HASH_RE.fullmatch(item["value_hash"]) is None
            for item in decoded
        )
    ):
        raise RuntimeError("private customer turn journal is invalid")
    material = tuple((item["name"], item["value_hash"]) for item in decoded)
    names = tuple(item[0] for item in material)
    if tuple(name for name in _PRIVATE_FACT_ORDER if name in names) != names:
        raise RuntimeError("private customer turn journal is invalid")
    return material


def _journal_fact_names(value: object) -> tuple[str, ...]:
    return tuple(item[0] for item in _journal_fact_material(value))


def _turn_content_hash(
    *,
    lead_id: str,
    source_turn_id: str,
    source_event_hash: str,
    fact_material: tuple[tuple[str, str], ...],
) -> str:
    return _domain_hash(
        b"v2-private-customer-fact-turn-v2",
        _canonical_json(
            {
                "facts": [
                    {"name": name, "value_hash": value_hash}
                    for name, value_hash in fact_material
                ],
                "lead_id": lead_id,
                "source_event_hash": source_event_hash,
                "source_turn_id": source_turn_id,
            }
        ),
    )


class SQLitePrivateCustomerFactStore:
    """Physically separate, private, idempotent owner for fallback customer facts."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("private customer store path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path: Path | None = path
        self._closed = False
        self._initialize_connection(str(path))

    @classmethod
    def open_memory(cls) -> "SQLitePrivateCustomerFactStore":
        store = cls.__new__(cls)
        store.path = None
        store._closed = False
        store._initialize_connection(":memory:")
        return store

    def _initialize_connection(self, target: str) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(target, isolation_level=None)
            connection.execute("PRAGMA busy_timeout=5000")
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if row[0] in _EXPECTED_SCHEMA
            }
            if existing:
                _validate_schema(connection)
            connection.executescript(_SCHEMA)
            _validate_schema(connection)
        except (sqlite3.DatabaseError, RuntimeError):
            if connection is not None:
                connection.close()
            self._closed = True
            raise RuntimeError("private customer store initialization failed") from None
        self._connection = connection

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("private customer store is closed")

    def load(self, lead_id: str) -> PrivateCustomerFactSnapshot:
        self._require_open()
        canonical_lead = _identifier(lead_id, "lead_id")
        try:
            rows = self._connection.execute(
                "SELECT f.fact_name,f.private_value,f.value_hash,f.source_turn_id,"
                "f.source_event_hash,f.revision,f.persisted_at,t.source_event_hash,"
                "t.fact_names_json,t.private_content_hash,t.persisted_at "
                "FROM private_customer_facts AS f "
                "LEFT JOIN private_customer_fact_turns AS t "
                "ON t.lead_id=f.lead_id AND t.source_turn_id=f.source_turn_id "
                "WHERE f.lead_id=? ORDER BY "
                "CASE f.fact_name WHEN 'full_name' THEN 1 WHEN 'email' THEN 2 ELSE 3 END",
                (canonical_lead,),
            ).fetchall()
        except sqlite3.DatabaseError:
            raise RuntimeError("private customer store read failed") from None
        values: dict[str, str] = {}
        sources: list[tuple[str, str]] = []
        canonicalizers = {
            "full_name": canonical_full_name,
            "email": canonical_email,
            "country_code": canonical_country_code,
        }
        for row in rows:
            (
                name,
                value,
                value_hash,
                source_turn_id,
                source_event_hash,
                revision,
                persisted_at,
                journal_event_hash,
                journal_names_json,
                journal_content_hash,
                journal_persisted_at,
            ) = row
            if name not in _PRIVATE_FACTS or name in values:
                raise RuntimeError("private customer store row identity is invalid")
            try:
                canonical = canonicalizers[name](value)
                _identifier(source_turn_id, "source_turn_id")
                _hash(source_event_hash, "source_event_hash")
                _hash(value_hash, "value_hash")
                _hash(journal_content_hash, "private_content_hash")
                _utc(datetime.fromisoformat(persisted_at), "persisted_at")
                _utc(datetime.fromisoformat(journal_persisted_at), "persisted_at")
                journal_material = _journal_fact_material(journal_names_json)
            except (PrivateCustomerFactValidationError, RuntimeError, TypeError, ValueError):
                raise RuntimeError("private customer store row is invalid") from None
            expected_journal_hash = _turn_content_hash(
                lead_id=canonical_lead,
                source_turn_id=source_turn_id,
                source_event_hash=source_event_hash,
                fact_material=journal_material,
            )
            if (
                canonical != value
                or value_hash != _value_hash(value)
                or journal_event_hash != source_event_hash
                or dict(journal_material).get(name) != value_hash
                or journal_content_hash != expected_journal_hash
                or type(revision) is not int
                or revision < 1
            ):
                raise RuntimeError("private customer store row is invalid")
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
        fact_material = tuple((name, _value_hash(value)) for name, value in rows)
        payload_hash = _turn_content_hash(
            lead_id=canonical_lead,
            source_turn_id=canonical_turn,
            source_event_hash=canonical_event_hash,
            fact_material=fact_material,
        )
        names_json = _canonical_json(
            [
                {"name": name, "value_hash": value_hash}
                for name, value_hash in fact_material
            ]
        ).decode("utf-8")
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
                value_hash = _value_hash(value)
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
        return _journal_fact_names(row[0])

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
