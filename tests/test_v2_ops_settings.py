from __future__ import annotations

from pathlib import Path

import pytest

from v2_ops.auth import hash_password
from v2_ops.settings import OpsWebSettings


def _valid_env() -> dict[str, str]:
    return {
        "V2_OPS_USERNAME": "ops-admin",
        "V2_OPS_PASSWORD_HASH": hash_password("password-123", salt=b"s" * 16),
        "V2_OPS_SESSION_KEY_HEX": "11" * 32,
        "V2_OPS_TRACE_KEY_HEX": "22" * 32,
        "V2_OPS_TRACE_PATH": "/data/ops/v2-ops-trace.sqlite3",
        "V2_OPS_SECURE_COOKIE": "true",
        "V2_OPS_RELEASE_SHA": "a" * 40,
        "V2_OPS_IMAGE_DIGEST": "sha256:" + "b" * 64,
        "V2_OPS_CONFIG_FINGERPRINT": "c" * 64,
    }


def test_records_path_is_optional_but_absolute_when_present() -> None:
    env = _valid_env()
    without = OpsWebSettings.from_env(env)
    assert without.records_path is None

    env["V2_OPS_RECORDS_PATH"] = "/data/records"
    assert OpsWebSettings.from_env(env).records_path == Path("/data/records")

    env["V2_OPS_RECORDS_PATH"] = "records"
    with pytest.raises(ValueError, match="records path must be absolute"):
        OpsWebSettings.from_env(env)


def test_records_path_rejects_non_path_values() -> None:
    settings = OpsWebSettings.from_env(_valid_env())
    with pytest.raises(ValueError, match="records path must be absolute"):
        OpsWebSettings(
            username=settings.username,
            password_hash=settings.password_hash,
            session_key=settings.session_key,
            trace_path=settings.trace_path,
            trace_key=settings.trace_key,
            session_ttl=settings.session_ttl,
            secure_cookie=settings.secure_cookie,
            stale_after=settings.stale_after,
            release_sha=settings.release_sha,
            image_digest=settings.image_digest,
            config_fingerprint=settings.config_fingerprint,
            records_path="/data/records",  # type: ignore[arg-type]
        )
