from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v2_ops.auth import LoginLimiter, SessionCodec, hash_password, verify_password
from v2_ops.settings import OpsWebSettings


def test_password_hash_is_scrypt_and_round_trips() -> None:
    encoded = hash_password("correct horse battery staple", salt=b"s" * 16)

    assert encoded.startswith("scrypt$n=16384$r=8$p=1$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong", encoded) is False


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "pbkdf2$bad",
        "scrypt$n=1$r=8$p=1$bad$bad",
        "scrypt$n=16384$r=8$p=1$%%%$%%%",
    ],
)
def test_password_hash_parser_fails_closed(encoded: str) -> None:
    assert verify_password("password", encoded) is False


def test_session_is_signed_expiring_and_bound_to_csrf() -> None:
    now = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
    codec = SessionCodec(b"k" * 32, ttl=timedelta(minutes=15))

    token = codec.issue(username="ops", csrf="csrf-token", now=now)
    claims = codec.verify(token, now=now + timedelta(minutes=1))

    assert claims.username == "ops"
    assert claims.csrf == "csrf-token"
    assert codec.verify(token + "x", now=now + timedelta(minutes=1)) is None
    assert codec.verify(token, now=now + timedelta(minutes=16)) is None
    assert SessionCodec(b"z" * 32, ttl=timedelta(minutes=15)).verify(token, now=now) is None


def test_login_limiter_is_bounded_and_recovers_after_window() -> None:
    now = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
    limiter = LoginLimiter(max_failures=2, window=timedelta(minutes=1), max_entries=2)

    assert limiter.allowed("client-a", now=now)
    limiter.failure("client-a", now=now)
    limiter.failure("client-a", now=now)
    assert not limiter.allowed("client-a", now=now)
    assert limiter.allowed("client-a", now=now + timedelta(minutes=2))

    limiter.failure("client-b", now=now)
    limiter.failure("client-c", now=now)
    assert limiter.entry_count <= 2


def test_web_settings_load_strict_independent_credentials_and_absolute_store(
    tmp_path,
) -> None:
    trace_path = (tmp_path / "ops.sqlite3").resolve()
    settings = OpsWebSettings.from_env(
        {
            "V2_OPS_USERNAME": "ops-admin",
            "V2_OPS_PASSWORD_HASH": hash_password(
                "password-123", salt=b"s" * 16
            ),
            "V2_OPS_SESSION_KEY_HEX": "6b" * 32,
            "V2_OPS_TRACE_PATH": str(trace_path),
            "V2_OPS_TRACE_KEY_HEX": "74" * 32,
            "V2_OPS_RELEASE_SHA": "a" * 40,
            "V2_OPS_IMAGE_DIGEST": "sha256:" + "b" * 64,
            "V2_OPS_CONFIG_FINGERPRINT": "c" * 64,
        }
    )

    assert settings.username == "ops-admin"
    assert settings.trace_path == trace_path
    assert settings.session_key == b"k" * 32
    assert settings.trace_key == b"t" * 32
    assert settings.release_sha == "a" * 40
    assert settings.image_digest == "sha256:" + "b" * 64
    assert settings.config_fingerprint == "c" * 64


@pytest.mark.parametrize(
    "override",
    [
        {"V2_OPS_USERNAME": "x"},
        {"V2_OPS_TRACE_PATH": "relative.sqlite3"},
        {"V2_OPS_SESSION_KEY_HEX": "00"},
        {"V2_OPS_TRACE_KEY_HEX": "00"},
        {"V2_OPS_SECURE_COOKIE": "yes"},
    ],
)
def test_web_settings_fail_closed_on_invalid_inputs(tmp_path, override) -> None:
    values = {
        "V2_OPS_USERNAME": "ops-admin",
        "V2_OPS_PASSWORD_HASH": hash_password(
            "password-123", salt=b"s" * 16
        ),
        "V2_OPS_SESSION_KEY_HEX": "6b" * 32,
        "V2_OPS_TRACE_PATH": str((tmp_path / "ops.sqlite3").resolve()),
        "V2_OPS_TRACE_KEY_HEX": "74" * 32,
    }
    values.update(override)

    with pytest.raises((TypeError, ValueError)):
        OpsWebSettings.from_env(values)


def test_web_settings_reject_malformed_scrypt_hash_with_valid_prefix(tmp_path) -> None:
    with pytest.raises(ValueError):
        OpsWebSettings(
            username="ops-admin",
            password_hash="scrypt$n=16384$r=8$p=1$broken$broken",
            session_key=b"k" * 32,
            trace_path=(tmp_path / "ops.sqlite3").resolve(),
            trace_key=b"t" * 32,
        )
