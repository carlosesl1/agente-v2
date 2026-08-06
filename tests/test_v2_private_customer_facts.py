from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from reservation_boundary import StringSlot, TypedFact
from v2_application.private_customer_facts import (
    PrivateCustomerFactIdentityConflict,
    PrivateCustomerFactValidationError,
    SQLitePrivateCustomerFactStore,
    canonical_country_code,
    canonical_email,
    canonical_full_name,
)
from v2_contracts.model import ModelFact


NOW = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
LEAD_ID = "manychat:synthetic-profile-001"
TURN_ID = "batch:private-profile-001"
EVENT_HASH = "a" * 64
NAME = "Pessoa Sintética Silva"
EMAIL = "synthetic.profile@example.invalid"
COUNTRY = "BR"


def _facts(
    *,
    name: str = NAME,
    email: str = EMAIL,
    country: str = COUNTRY,
) -> tuple[ModelFact, ...]:
    return (
        ModelFact("full_name", name),
        ModelFact("email", email),
        ModelFact("country_code", country),
    )


def _passenger_manifest_fact() -> TypedFact:
    manifest = json.dumps(
        {
            "schema": "v2-passenger-manifest-v1",
            "adults": 1,
            "children": 0,
            "passengers": [
                {
                    "position": 1,
                    "participant_type": "adult",
                    "full_name": NAME,
                    "birth_date": "1990-01-02",
                    "gender": "f",
                    "country_code": COUNTRY,
                }
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return TypedFact("passenger_manifest", StringSlot(manifest), "b" * 64)


def test_private_passenger_manifest_round_trips_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "private-customer.sqlite3"
    first = SQLitePrivateCustomerFactStore(path)
    fact = _passenger_manifest_fact()
    try:
        assert first.load_passenger_manifest(LEAD_ID) is None
        assert first.persist_passenger_manifest(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            fact=fact,
            persisted_at=NOW,
        ) is True
        assert first.load_passenger_manifest(LEAD_ID) == fact
        assert first.persist_passenger_manifest(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            fact=fact,
            persisted_at=NOW,
        ) is False
    finally:
        first.close()

    restarted = SQLitePrivateCustomerFactStore(path)
    try:
        assert restarted.load_passenger_manifest(LEAD_ID) == fact
    finally:
        restarted.close()


def test_private_customer_canonicalizers_are_strict_and_do_not_invent_values() -> None:
    assert canonical_full_name("  Pessoa   Sintética  Silva  ") == NAME
    assert canonical_email(" Synthetic.Profile@Example.Invalid ") == EMAIL
    assert canonical_country_code(" br ") == COUNTRY

    for invalid in ("Pessoa", "", "Pessoa\x00 Silva", "X" * 201 + " Y"):
        with pytest.raises(PrivateCustomerFactValidationError, match="full name"):
            canonical_full_name(invalid)
    for invalid in ("missing-at.invalid", "@example.invalid", "a@", "a b@example.invalid"):
        with pytest.raises(PrivateCustomerFactValidationError, match="email"):
            canonical_email(invalid)
    for invalid in ("BRA", "1R", ""):
        with pytest.raises(PrivateCustomerFactValidationError, match="country"):
            canonical_country_code(invalid)


def test_private_store_round_trip_is_exact_private_and_presence_only(tmp_path: Path) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    try:
        written = store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
        loaded = store.load(LEAD_ID)

        assert written.snapshot == loaded
        assert loaded.full_name == NAME
        assert loaded.email == EMAIL
        assert loaded.country_code == COUNTRY
        assert loaded.present_fact_names == ("full_name", "email", "country_code")
        assert loaded.source_turn_for("full_name") == TURN_ID
        assert loaded.source_turn_for("email") == TURN_ID
        assert loaded.source_turn_for("country_code") == TURN_ID
        assert written.supplied_in_turn == ("full_name", "email", "country_code")
        assert written.changed_in_turn == ("full_name", "email", "country_code")
        assert written.replayed is False
        assert len(loaded.content_hash) == 64

        for private_value in (NAME, EMAIL, COUNTRY):
            assert private_value not in repr(loaded)
            assert private_value not in repr(written)
            assert private_value not in str(loaded.public_presence())
    finally:
        store.close()


def test_same_turn_is_idempotent_and_crash_retry_remains_collection_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private-customer.sqlite3"
    first = SQLitePrivateCustomerFactStore(path)
    try:
        original = first.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
    finally:
        first.close()

    restarted = SQLitePrivateCustomerFactStore(path)
    try:
        replay = restarted.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW + timedelta(seconds=5),
        )
        assert replay.snapshot == original.snapshot
        assert replay.supplied_in_turn == ("full_name", "email", "country_code")
        assert replay.changed_in_turn == ()
        assert replay.replayed is True
        assert restarted.turn_supplied_fact_names(LEAD_ID, TURN_ID) == (
            "full_name",
            "email",
            "country_code",
        )
    finally:
        restarted.close()


def test_same_turn_divergence_fails_without_echoing_private_values(tmp_path: Path) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    try:
        store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
        divergent_name = "Outra Pessoa Sintética"
        with pytest.raises(PrivateCustomerFactIdentityConflict) as payload_error:
            store.persist_turn(
                lead_id=LEAD_ID,
                source_turn_id=TURN_ID,
                source_event_hash=EVENT_HASH,
                facts=_facts(name=divergent_name),
                persisted_at=NOW,
            )
        with pytest.raises(PrivateCustomerFactIdentityConflict) as event_error:
            store.persist_turn(
                lead_id=LEAD_ID,
                source_turn_id=TURN_ID,
                source_event_hash="b" * 64,
                facts=_facts(),
                persisted_at=NOW,
            )
        rendered = repr((payload_error.value, event_error.value))
        assert NAME not in rendered
        assert divergent_name not in rendered
        assert EMAIL not in rendered
    finally:
        store.close()


def test_store_rejects_phone_and_non_profile_facts_without_persisting(
    tmp_path: Path,
) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    try:
        for fact in (
            ModelFact("phone_e164", "+12025550123"),
            ModelFact("service", "hostel"),
        ):
            with pytest.raises(PrivateCustomerFactValidationError, match="catalog"):
                store.persist_turn(
                    lead_id=LEAD_ID,
                    source_turn_id=TURN_ID,
                    source_event_hash=EVENT_HASH,
                    facts=(fact,),
                    persisted_at=NOW,
                )
        assert store.load(LEAD_ID).present_fact_names == ()
    finally:
        store.close()


def test_later_turn_updates_one_field_without_losing_other_values(tmp_path: Path) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    try:
        store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
        next_turn = "batch:private-profile-002"
        result = store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=next_turn,
            source_event_hash="c" * 64,
            facts=(ModelFact("email", "corrected@example.invalid"),),
            persisted_at=NOW + timedelta(minutes=1),
        )

        assert result.snapshot.full_name == NAME
        assert result.snapshot.email == "corrected@example.invalid"
        assert result.snapshot.country_code == COUNTRY
        assert result.snapshot.source_turn_for("email") == next_turn
        assert result.supplied_in_turn == ("email",)
        assert result.changed_in_turn == ("email",)
    finally:
        store.close()


def test_store_rejects_incompatible_existing_schema_during_open(tmp_path: Path) -> None:
    path = tmp_path / "private-customer-malformed.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE private_customer_facts (lead_id TEXT PRIMARY KEY)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="initialization failed"):
        SQLitePrivateCustomerFactStore(path)


def test_store_rejects_schema_with_required_checks_removed(tmp_path: Path) -> None:
    path = tmp_path / "private-customer-without-checks.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE private_customer_fact_turns (
                lead_id TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                source_event_hash TEXT NOT NULL,
                fact_names_json TEXT NOT NULL,
                private_content_hash TEXT NOT NULL,
                persisted_at TEXT NOT NULL,
                PRIMARY KEY (lead_id, source_turn_id)
            ) STRICT;
            CREATE TABLE private_customer_facts (
                lead_id TEXT NOT NULL,
                fact_name TEXT NOT NULL,
                private_value TEXT NOT NULL,
                value_hash TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                source_event_hash TEXT NOT NULL,
                revision INTEGER NOT NULL,
                persisted_at TEXT NOT NULL,
                PRIMARY KEY (lead_id, fact_name)
            ) STRICT;
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="initialization failed"):
        SQLitePrivateCustomerFactStore(path)


def test_store_load_rejects_tampered_value_and_unbacked_source(tmp_path: Path) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer-tamper.sqlite3")
    try:
        store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
        store._connection.execute(
            "UPDATE private_customer_facts SET private_value=? "
            "WHERE lead_id=? AND fact_name='email'",
            ("tampered@example.invalid", LEAD_ID),
        )
        with pytest.raises(RuntimeError, match="store row is invalid"):
            store.load(LEAD_ID)

        store._connection.execute(
            "UPDATE private_customer_facts SET private_value=?,source_turn_id=? "
            "WHERE lead_id=? AND fact_name='email'",
            (EMAIL, "batch:unbacked", LEAD_ID),
        )
        with pytest.raises(RuntimeError, match="store row is invalid"):
            store.load(LEAD_ID)
    finally:
        store.close()


def test_store_load_rejects_value_rehashed_without_matching_turn_journal(
    tmp_path: Path,
) -> None:
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer-rehash.sqlite3")
    tampered = "tampered@example.invalid"
    tampered_hash = hashlib.sha256(
        b"v2-private-customer-fact-value-v1\0" + tampered.encode("utf-8")
    ).hexdigest()
    try:
        store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
        store._connection.execute(
            "UPDATE private_customer_facts SET private_value=?,value_hash=? "
            "WHERE lead_id=? AND fact_name='email'",
            (tampered, tampered_hash, LEAD_ID),
        )

        with pytest.raises(RuntimeError, match="store row is invalid") as error:
            store.load(LEAD_ID)
        assert tampered not in repr(error.value)
        assert EMAIL not in repr(error.value)
    finally:
        store.close()


def test_turn_presence_rejects_tampered_no_change_journal(tmp_path: Path) -> None:
    store = SQLitePrivateCustomerFactStore(
        tmp_path / "private-customer-no-change-journal.sqlite3"
    )
    second_turn = "batch:private-profile-002"
    second_event_hash = "b" * 64
    try:
        store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=TURN_ID,
            source_event_hash=EVENT_HASH,
            facts=_facts(),
            persisted_at=NOW,
        )
        repeated = store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id=second_turn,
            source_event_hash=second_event_hash,
            facts=(ModelFact("email", EMAIL),),
            persisted_at=NOW + timedelta(seconds=1),
        )
        assert repeated.changed_in_turn == ()
        assert store.turn_supplied_fact_names(LEAD_ID, second_turn) == ("email",)

        store._connection.execute(
            "UPDATE private_customer_fact_turns SET fact_names_json='[]' "
            "WHERE lead_id=? AND source_turn_id=?",
            (LEAD_ID, second_turn),
        )
        with pytest.raises(RuntimeError, match="turn journal is invalid") as error:
            store.turn_supplied_fact_names(LEAD_ID, second_turn)
        assert NAME not in repr(error.value)
        assert EMAIL not in repr(error.value)
    finally:
        store.close()
