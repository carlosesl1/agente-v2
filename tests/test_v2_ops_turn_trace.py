from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2ProcessRole, V2Settings
from v2_ops.recording import NullOpsRecorder, SQLiteOpsRecorder


KEY_HEX = "42" * 32


def _settings(tmp_path: Path, **overrides: object) -> V2Settings:
    values: dict[str, object] = {
        "webhook_secret": "test-webhook-secret",
        "sqlite_path": (tmp_path / "state" / "inbox.sqlite3").resolve(),
        "process_role": V2ProcessRole.COMBINED,
    }
    values.update(overrides)
    return V2Settings(**values)


def test_trace_settings_are_disabled_by_default() -> None:
    settings = V2Settings(
        webhook_secret="test-webhook-secret",
        sqlite_path=Path("/tmp/v2-ops-settings-disabled/inbox.sqlite3"),
    )

    assert settings.ops_trace_path is None
    assert settings.ops_trace_key == b""
    assert settings.ops_trace_full_content is False


@pytest.mark.parametrize(
    ("path", "key", "expected"),
    [
        (Path("relative.sqlite3"), bytes.fromhex(KEY_HEX), "absolute"),
        (Path("/tmp/trace.sqlite3"), b"", "required"),
        (None, bytes.fromhex(KEY_HEX), "without path"),
        (Path("/tmp/trace.sqlite3"), b"short", "32-byte"),
    ],
)
def test_trace_settings_reject_invalid_path_key_matrix(
    path: Path | None,
    key: bytes,
    expected: str,
) -> None:
    with pytest.raises(ValueError, match=expected):
        V2Settings(
            webhook_secret="test-webhook-secret",
            sqlite_path=Path("/tmp/v2-ops-settings-matrix/inbox.sqlite3"),
            ops_trace_path=path,
            ops_trace_key=key,
        )


def test_trace_settings_require_exact_full_content_bool(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="full content"):
        _settings(tmp_path, ops_trace_full_content=1)


def test_trace_path_is_distinct_from_every_business_owner(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for path in settings.sqlite_paths.values():
        with pytest.raises(ValueError, match="distinct"):
            replace(
                settings,
                ops_trace_path=path,
                ops_trace_key=bytes.fromhex(KEY_HEX),
            )


def test_api_environment_ignores_worker_trace_secret(tmp_path: Path) -> None:
    env = {
        "V2_SQLITE_PATH": str((tmp_path / "inbox.sqlite3").resolve()),
        "V2_MANYCHAT_WEBHOOK_SECRET": "api-ingress-secret",
        "V2_OPS_TRACE_PATH": str((tmp_path / "ops.sqlite3").resolve()),
        "V2_OPS_TRACE_KEY_HEX": KEY_HEX,
        "V2_OPS_TRACE_FULL_CONTENT": "true",
    }

    settings = V2Settings.from_env(env, process_role=V2ProcessRole.API)

    assert settings.ops_trace_path is None
    assert settings.ops_trace_key == b""
    assert settings.ops_trace_full_content is False


def test_worker_container_owns_one_trace_writer_and_closes_it(tmp_path: Path) -> None:
    trace_path = (tmp_path / "ops" / "trace.sqlite3").resolve()
    settings = _settings(
        tmp_path,
        ops_trace_path=trace_path,
        ops_trace_key=bytes.fromhex(KEY_HEX),
    )

    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    recorder = container.ops_recorder
    try:
        assert type(recorder) is SQLiteOpsRecorder
        assert container._ops_trace_writer is recorder._writer
        assert trace_path.exists()
    finally:
        container.close()

    assert recorder._writer._connection is None
    container.close()


def test_disabled_worker_and_api_use_stateless_null_recorder(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    worker = V2Container.open(settings=settings, role=V2Role.WORKER)
    api = V2Container.open(settings=settings, role=V2Role.API)
    try:
        assert type(worker.ops_recorder) is NullOpsRecorder
        assert type(api.ops_recorder) is NullOpsRecorder
        assert worker._ops_trace_writer is None
        assert api._ops_trace_writer is None
    finally:
        api.close()
        worker.close()
