from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

import deploy.rollback_harness as rollback_module
from deploy.rollback_harness import RollbackSafetyError, rollback_to_previous


CURRENT_SHA = "c" * 40
PREVIOUS_SHA = "a" * 40
CURRENT_DIGEST = "sha256:" + "d" * 64
PREVIOUS_DIGEST = "sha256:" + "e" * 64


def _metadata(sha: str, digest: str) -> dict[str, object]:
    return {
        "schema": "v2-candidate-metadata-v1",
        "git_sha": sha,
        "image_ref": f"registry.invalid/agente-v2@{digest}",
        "image_digest": digest,
    }


def _receipt_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE receipts (id TEXT PRIMARY KEY, digest TEXT)")
        connection.execute("INSERT INTO receipts VALUES ('receipt:1', ?)", ("f" * 64,))
        connection.commit()
    finally:
        connection.close()


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    workspace = tmp_path / "rollback"
    workspace.mkdir()
    (workspace / ".v2-rollback-sandbox").write_text(
        "v2-rollback-sandbox-v1\n", encoding="utf-8"
    )
    active = workspace / "active-candidate.json"
    previous = workspace / "previous-candidate.json"
    active.write_text(
        json.dumps(_metadata(CURRENT_SHA, CURRENT_DIGEST)), encoding="utf-8"
    )
    previous.write_text(
        json.dumps(_metadata(PREVIOUS_SHA, PREVIOUS_DIGEST)), encoding="utf-8"
    )
    state = workspace / "state"
    state.mkdir()
    database = state / "receipts.sqlite3"
    _receipt_db(database)
    return workspace, active, previous, database


def test_rollback_restores_previous_metadata_without_touching_sqlite(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "rollback"
    workspace.mkdir()
    (workspace / ".v2-rollback-sandbox").write_text(
        "v2-rollback-sandbox-v1\n", encoding="utf-8"
    )
    active = workspace / "active-candidate.json"
    previous = workspace / "previous-candidate.json"
    active.write_text(json.dumps(_metadata(CURRENT_SHA, CURRENT_DIGEST)), encoding="utf-8")
    previous.write_text(
        json.dumps(_metadata(PREVIOUS_SHA, PREVIOUS_DIGEST)), encoding="utf-8"
    )
    state = workspace / "state"
    state.mkdir()
    db = state / "receipts.sqlite3"
    _receipt_db(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()

    receipt = rollback_to_previous(
        workspace=workspace,
        expected_current_sha=CURRENT_SHA,
    )

    assert json.loads(active.read_text(encoding="utf-8")) == _metadata(
        PREVIOUS_SHA, PREVIOUS_DIGEST
    )
    assert json.loads(previous.read_text(encoding="utf-8")) == _metadata(
        PREVIOUS_SHA, PREVIOUS_DIGEST
    )
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert receipt.from_git_sha == CURRENT_SHA
    assert receipt.to_git_sha == PREVIOUS_SHA
    assert receipt.sqlite_fingerprints_before == receipt.sqlite_fingerprints_after
    assert receipt.sqlite_fingerprints_after == {"state/receipts.sqlite3": before}
    assert not (workspace / ".active-candidate.json.rollback-backup").exists()
    assert not (workspace / ".active-candidate.json.rollback-tmp").exists()


def test_rollback_rejects_non_temporary_or_unmarked_workspace(tmp_path: Path) -> None:
    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(workspace=unmarked, expected_current_sha=CURRENT_SHA)

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(workspace=Path("/"), expected_current_sha=CURRENT_SHA)


def test_rollback_fails_closed_on_current_identity_mismatch(tmp_path: Path) -> None:
    workspace = tmp_path / "rollback"
    workspace.mkdir()
    (workspace / ".v2-rollback-sandbox").write_text(
        "v2-rollback-sandbox-v1\n", encoding="utf-8"
    )
    active = workspace / "active-candidate.json"
    previous = workspace / "previous-candidate.json"
    original = json.dumps(_metadata(CURRENT_SHA, CURRENT_DIGEST))
    active.write_text(original, encoding="utf-8")
    previous.write_text(
        json.dumps(_metadata(PREVIOUS_SHA, PREVIOUS_DIGEST)), encoding="utf-8"
    )

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(
            workspace=workspace,
            expected_current_sha="x" * 40,
        )

    assert active.read_text(encoding="utf-8") == original


def test_workspace_symlink_swap_cannot_redirect_atomic_metadata_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "rollback"
    outside = tmp_path / "outside"
    for root in (workspace, outside):
        root.mkdir()
        (root / ".v2-rollback-sandbox").write_text(
            "v2-rollback-sandbox-v1\n", encoding="utf-8"
        )
        (root / "active-candidate.json").write_text(
            json.dumps(_metadata(CURRENT_SHA, CURRENT_DIGEST)), encoding="utf-8"
        )
        (root / "previous-candidate.json").write_text(
            json.dumps(_metadata(PREVIOUS_SHA, PREVIOUS_DIGEST)), encoding="utf-8"
        )
        (root / "state").mkdir()
        _receipt_db(root / "state/receipts.sqlite3")
    outside_active_before = (outside / "active-candidate.json").read_bytes()
    displaced = tmp_path / "displaced"
    real_exchange = rollback_module._rename_exchange_at
    swapped = False

    def exchange_with_swap(directory_fd: int, left: str, right: str) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            workspace.rename(displaced)
            workspace.symlink_to(outside, target_is_directory=True)
        real_exchange(directory_fd, left, right)

    monkeypatch.setattr(
        rollback_module,
        "_rename_exchange_at",
        exchange_with_swap,
    )

    receipt = rollback_to_previous(
        workspace=workspace,
        expected_current_sha=CURRENT_SHA,
    )

    assert receipt.to_git_sha == PREVIOUS_SHA
    assert (outside / "active-candidate.json").read_bytes() == outside_active_before
    assert json.loads((displaced / "active-candidate.json").read_text()) == _metadata(
        PREVIOUS_SHA,
        PREVIOUS_DIGEST,
    )
    assert not (displaced / ".active-candidate.json.rollback-backup").exists()
    assert not (displaced / ".active-candidate.json.rollback-tmp").exists()


def test_fingerprint_divergence_restores_current_candidate_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, active, _previous, _database = _workspace(tmp_path)
    real_fingerprints = rollback_module._sqlite_fingerprints
    calls = 0

    def divergent_fingerprints(directory_fd: int) -> dict[str, str]:
        nonlocal calls
        calls += 1
        result = real_fingerprints(directory_fd)
        if calls == 2:
            return {**result, "state/receipts.sqlite3": "f" * 64}
        return result

    monkeypatch.setattr(
        rollback_module,
        "_sqlite_fingerprints",
        divergent_fingerprints,
    )

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(
            workspace=workspace,
            expected_current_sha=CURRENT_SHA,
        )

    assert json.loads(active.read_text(encoding="utf-8")) == _metadata(
        CURRENT_SHA,
        CURRENT_DIGEST,
    )


def test_post_commit_fingerprint_read_error_restores_current_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, active, _previous, _database = _workspace(tmp_path)
    real_fingerprints = rollback_module._sqlite_fingerprints
    calls = 0

    def failing_fingerprints(directory_fd: int) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected observation failure")
        return real_fingerprints(directory_fd)

    monkeypatch.setattr(rollback_module, "_sqlite_fingerprints", failing_fingerprints)

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(
            workspace=workspace,
            expected_current_sha=CURRENT_SHA,
        )

    assert json.loads(active.read_text(encoding="utf-8")) == _metadata(
        CURRENT_SHA,
        CURRENT_DIGEST,
    )


def test_hardlinked_current_and_previous_noop_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "rollback"
    workspace.mkdir()
    (workspace / ".v2-rollback-sandbox").write_text(
        "v2-rollback-sandbox-v1\n", encoding="utf-8"
    )
    active = workspace / "active-candidate.json"
    previous = workspace / "previous-candidate.json"
    active.write_text(
        json.dumps(_metadata(CURRENT_SHA, CURRENT_DIGEST)), encoding="utf-8"
    )
    previous.hardlink_to(active)

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(
            workspace=workspace,
            expected_current_sha=CURRENT_SHA,
        )

    assert json.loads(active.read_text(encoding="utf-8")) == _metadata(
        CURRENT_SHA,
        CURRENT_DIGEST,
    )


def test_no_fallible_directory_sync_occurs_after_backup_is_unlinked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, active, _previous, _database = _workspace(tmp_path)
    real_fsync = rollback_module.os.fsync
    injected = False

    def fail_if_backup_is_already_gone(descriptor: int) -> None:
        nonlocal injected
        opened = rollback_module.os.fstat(descriptor)
        backup = workspace / ".active-candidate.json.rollback-backup"
        current = json.loads(active.read_text(encoding="utf-8"))["git_sha"]
        if (
            rollback_module.stat.S_ISDIR(opened.st_mode)
            and not backup.exists()
            and current == PREVIOUS_SHA
        ):
            injected = True
            raise OSError("synthetic sync failure after backup unlink")
        real_fsync(descriptor)

    monkeypatch.setattr(rollback_module.os, "fsync", fail_if_backup_is_already_gone)

    receipt = rollback_to_previous(
        workspace=workspace,
        expected_current_sha=CURRENT_SHA,
    )

    assert injected is False
    assert receipt.to_git_sha == PREVIOUS_SHA
    assert json.loads(active.read_text(encoding="utf-8"))["git_sha"] == PREVIOUS_SHA


def test_concurrent_active_metadata_swap_is_not_clobbered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, active, _previous, _database = _workspace(tmp_path)
    newer_sha = "f" * 40
    newer_digest = "sha256:" + "9" * 64
    real_create_backup = rollback_module._create_active_backup_at
    swapped = False

    def swap_then_backup(
        directory_fd: int,
        expected_active_identity: tuple[int, int],
    ) -> None:
        nonlocal swapped
        replacement = workspace / "newer-candidate.json"
        replacement.write_text(
            json.dumps(_metadata(newer_sha, newer_digest)),
            encoding="utf-8",
        )
        replacement.replace(active)
        swapped = True
        real_create_backup(directory_fd, expected_active_identity)

    monkeypatch.setattr(
        rollback_module,
        "_create_active_backup_at",
        swap_then_backup,
    )

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(
            workspace=workspace,
            expected_current_sha=CURRENT_SHA,
        )

    assert swapped is True
    assert json.loads(active.read_text(encoding="utf-8"))["git_sha"] == newer_sha


def test_atomic_publish_race_restores_concurrently_published_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, active, _previous, _database = _workspace(tmp_path)
    newer_sha = "f" * 40
    newer_digest = "sha256:" + "9" * 64
    replacement = workspace / "newer-candidate.json"
    replacement.write_text(
        json.dumps(_metadata(newer_sha, newer_digest)),
        encoding="utf-8",
    )
    real_replace = rollback_module.os.replace
    real_exchange = rollback_module._rename_exchange_at
    swapped = False

    def swap_at_publish(directory_fd: int, left: str, right: str) -> None:
        nonlocal swapped
        if not swapped:
            real_replace(replacement, active)
            swapped = True
        real_exchange(directory_fd, left, right)

    monkeypatch.setattr(
        rollback_module,
        "_rename_exchange_at",
        swap_at_publish,
    )

    with pytest.raises(RollbackSafetyError, match="rollback sandbox rejected"):
        rollback_to_previous(
            workspace=workspace,
            expected_current_sha=CURRENT_SHA,
        )

    assert swapped is True
    assert json.loads(active.read_text(encoding="utf-8"))["git_sha"] == newer_sha
