from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reservation_boundary import (
    ConversationProjection,
    ConversationStage,
    StringSlot,
    TypedFact,
)
from v2_application.conversation import (
    EffectiveCustomerResolution,
    resolve_effective_customer,
)
from v2_application.private_customer_facts import SQLitePrivateCustomerFactStore
from v2_contracts.model import ModelFact
from v2_contracts.profile import PrivateCustomerBinding


NOW = datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc)
LEAD_ID = "manychat:synthetic-effective-001"
PHONE = "+12025550123"
NAME = "Pessoa Efetiva Silva"
EMAIL = "effective.person@example.invalid"
COUNTRY = "BR"
FRAME_HASH = "f" * 64


def _profile(
    *,
    full_name: str | None = None,
    email: str | None = None,
    phone: str | None = PHONE,
    country: str | None = None,
) -> PrivateCustomerBinding:
    values = (full_name, email, phone, country)
    return PrivateCustomerBinding(
        binding_id="profile-binding:" + "a" * 64,
        content_hash="b" * 64,
        full_name=full_name,
        email=email,
        phone_e164=phone,
        country_code=country,
        observed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        complete=all(value is not None for value in values),
    )


def _projection(*facts: TypedFact) -> ConversationProjection:
    return ConversationProjection(
        stage=ConversationStage.RECEPTIONIST,
        desired_services=(),
        locale="pt-BR",
        facts=facts,
        reservation_execution_projection=None,
    )


def _private_snapshot(tmp_path: Path, *facts: ModelFact):
    store = SQLitePrivateCustomerFactStore(tmp_path / "private-customer.sqlite3")
    try:
        store.persist_turn(
            lead_id=LEAD_ID,
            source_turn_id="batch:effective-profile-001",
            source_event_hash="c" * 64,
            facts=tuple(facts),
            persisted_at=NOW,
        )
        return store.load(LEAD_ID)
    finally:
        store.close()


def test_phone_only_manychat_plus_private_fallback_resolves_exact_customer(
    tmp_path: Path,
) -> None:
    private = _private_snapshot(
        tmp_path,
        ModelFact("full_name", NAME),
        ModelFact("email", EMAIL),
        ModelFact("country_code", COUNTRY),
    )

    resolution = resolve_effective_customer(
        _profile(),
        _projection(),
        NOW,
        private_facts=private,
    )

    assert type(resolution) is EffectiveCustomerResolution
    assert resolution.ready is True
    assert resolution.missing_fields == ()
    assert resolution.conflicting_fields == ()
    assert resolution.customer is not None
    assert resolution.customer.full_name == NAME
    assert resolution.customer.email == EMAIL
    assert resolution.customer.phone_e164 == PHONE
    assert resolution.customer.country_code == COUNTRY
    assert resolution.customer.customer_ref.startswith("effective-customer:")
    for value in (NAME, EMAIL, PHONE, COUNTRY):
        assert value not in repr(resolution)
        assert value not in repr(resolution.customer)


def test_one_word_manychat_name_uses_private_full_name_fallback(tmp_path: Path) -> None:
    private = _private_snapshot(
        tmp_path,
        ModelFact("full_name", NAME),
        ModelFact("email", EMAIL),
        ModelFact("country_code", COUNTRY),
    )

    resolution = resolve_effective_customer(
        _profile(full_name="Pessoa"),
        _projection(),
        NOW,
        private_facts=private,
    )

    assert resolution.ready is True
    assert resolution.customer is not None
    assert resolution.customer.full_name == NAME


def test_missing_manychat_email_uses_private_email_fallback(tmp_path: Path) -> None:
    private = _private_snapshot(
        tmp_path,
        ModelFact("email", EMAIL),
    )

    resolution = resolve_effective_customer(
        _profile(full_name=NAME, country=COUNTRY),
        _projection(),
        NOW,
        private_facts=private,
    )

    assert resolution.ready is True
    assert resolution.customer is not None
    assert resolution.customer.email == EMAIL


def test_valid_manychat_and_divergent_private_values_fail_closed(tmp_path: Path) -> None:
    private = _private_snapshot(
        tmp_path,
        ModelFact("full_name", "Outra Pessoa Silva"),
        ModelFact("email", "other.person@example.invalid"),
        ModelFact("country_code", "US"),
    )

    resolution = resolve_effective_customer(
        _profile(full_name=NAME, email=EMAIL, country=COUNTRY),
        _projection(),
        NOW,
        private_facts=private,
    )

    assert resolution.ready is False
    assert resolution.customer is None
    assert resolution.missing_fields == ()
    assert resolution.conflicting_fields == ("full_name", "email", "country_code")


def test_missing_future_or_expired_authenticated_phone_never_resolves(
    tmp_path: Path,
) -> None:
    private = _private_snapshot(
        tmp_path,
        ModelFact("full_name", NAME),
        ModelFact("email", EMAIL),
        ModelFact("country_code", COUNTRY),
    )
    base = _profile()
    candidates = (
        replace(base, phone_e164=None, complete=False),
        replace(
            base,
            observed_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=5),
        ),
        replace(
            base,
            observed_at=NOW - timedelta(minutes=5),
            expires_at=NOW,
        ),
    )

    for candidate in candidates:
        resolution = resolve_effective_customer(
            candidate,
            _projection(
                TypedFact("phone_e164", StringSlot("+12025550124"), FRAME_HASH)
            ),
            NOW,
            private_facts=private,
        )
        assert resolution.ready is False
        assert resolution.customer is None
        assert "phone_e164" in resolution.missing_fields


def test_split_origin_customer_identity_changes_with_either_origin(
    tmp_path: Path,
) -> None:
    private = _private_snapshot(
        tmp_path,
        ModelFact("full_name", NAME),
        ModelFact("email", EMAIL),
        ModelFact("country_code", COUNTRY),
    )
    first = resolve_effective_customer(
        _profile(),
        _projection(),
        NOW,
        private_facts=private,
    )
    changed_binding = replace(
        _profile(),
        binding_id="profile-binding:" + "d" * 64,
        content_hash="e" * 64,
    )
    second = resolve_effective_customer(
        changed_binding,
        _projection(),
        NOW,
        private_facts=private,
    )
    changed_private = _private_snapshot(
        tmp_path / "other",
        ModelFact("full_name", "Pessoa Efetiva Souza"),
        ModelFact("email", EMAIL),
        ModelFact("country_code", COUNTRY),
    )
    third = resolve_effective_customer(
        _profile(),
        _projection(),
        NOW,
        private_facts=changed_private,
    )

    assert first.customer is not None
    assert second.customer is not None
    assert third.customer is not None
    assert len(
        {
            first.customer.customer_ref,
            second.customer.customer_ref,
            third.customer.customer_ref,
        }
    ) == 3


def test_legacy_country_projection_remains_read_compatible_without_private_snapshot() -> None:
    resolution = resolve_effective_customer(
        _profile(full_name=NAME, email=EMAIL),
        _projection(TypedFact("country_code", StringSlot(COUNTRY), FRAME_HASH)),
        NOW,
    )

    assert resolution.ready is True
    assert resolution.customer is not None
    assert resolution.customer.country_code == COUNTRY
