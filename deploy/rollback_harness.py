"""Hermetic metadata-only rollback harness for CI sandboxes."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE_REF = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")
_MARKER = b"v2-rollback-sandbox-v1\n"
_SCHEMA = "v2-candidate-metadata-v1"
_ACTIVE = "active-candidate.json"
_PREVIOUS = "previous-candidate.json"
_TEMPORARY = ".active-candidate.json.rollback-tmp"
_BACKUP = ".active-candidate.json.rollback-backup"
_RENAME_EXCHANGE = 2


class RollbackSafetyError(ValueError):
    """Closed failure for unsafe or divergent rollback inputs."""

    def __init__(self) -> None:
        super().__init__("rollback sandbox rejected")


class _ActiveIdentityChanged(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RollbackReceipt:
    from_git_sha: str
    to_git_sha: str
    sqlite_fingerprints_before: Mapping[str, str]
    sqlite_fingerprints_after: Mapping[str, str]


def _reject() -> RollbackSafetyError:
    return RollbackSafetyError()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _read_regular_snapshot_at(
    directory_fd: int,
    name: str,
) -> tuple[bytes, tuple[int, int]]:
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        dir_fd=directory_fd,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise _reject()
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks), (opened.st_dev, opened.st_ino)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _read_regular_at(directory_fd: int, name: str) -> bytes:
    return _read_regular_snapshot_at(directory_fd, name)[0]


def _strict_object(raw: bytes) -> dict[str, str]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise _reject()
            value[key] = item
        return value

    try:
        payload = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError, RollbackSafetyError) as exc:
        raise _reject() from exc
    if type(payload) is not dict or set(payload) != {
        "schema",
        "git_sha",
        "image_ref",
        "image_digest",
    }:
        raise _reject()
    if any(type(value) is not str for value in payload.values()):
        raise _reject()
    result = dict(payload)
    if (
        result["schema"] != _SCHEMA
        or _GIT_SHA.fullmatch(result["git_sha"]) is None
        or _DIGEST.fullmatch(result["image_digest"]) is None
        or _IMAGE_REF.fullmatch(result["image_ref"]) is None
        or not result["image_ref"].endswith("@" + result["image_digest"])
    ):
        raise _reject()
    return result


def _sqlite_fingerprints(directory_fd: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative_root, directories, files, current_fd in os.fwalk(
        ".",
        topdown=True,
        follow_symlinks=False,
        dir_fd=directory_fd,
    ):
        for name in directories:
            item = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if stat.S_ISLNK(item.st_mode):
                raise _reject()
        for name in files:
            if not (
                name.endswith(".sqlite3")
                or name.endswith(".sqlite3-wal")
                or name.endswith(".sqlite3-shm")
            ):
                continue
            listed = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISREG(listed.st_mode):
                raise _reject()
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            try:
                opened = os.fstat(descriptor)
                if (listed.st_dev, listed.st_ino) != (opened.st_dev, opened.st_ino):
                    raise _reject()
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            finally:
                os.close(descriptor)
            relative = Path(relative_root, name).as_posix()
            result[relative.removeprefix("./")] = digest.hexdigest()
    return dict(sorted(result.items()))


def _rename_exchange_at(directory_fd: int, left: str, right: str) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(library, "renameat2", None)
    if renameat2 is None:
        raise _reject()
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd,
        os.fsencode(left),
        directory_fd,
        os.fsencode(right),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _atomic_write_at(
    directory_fd: int,
    payload: Mapping[str, str],
    *,
    expected_destination_identity: tuple[int, int],
) -> None:
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    try:
        os.stat(_TEMPORARY, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise _reject()
    descriptor = os.open(
        _TEMPORARY,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_CLOEXEC
        | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    exchanged = False
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        destination = os.stat(_ACTIVE, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(destination.st_mode)
            or (destination.st_dev, destination.st_ino)
            != expected_destination_identity
        ):
            raise _ActiveIdentityChanged
        _rename_exchange_at(directory_fd, _TEMPORARY, _ACTIVE)
        exchanged = True
        captured = os.stat(_TEMPORARY, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(captured.st_mode)
            or (captured.st_dev, captured.st_ino)
            != expected_destination_identity
        ):
            _rename_exchange_at(directory_fd, _TEMPORARY, _ACTIVE)
            exchanged = False
            os.fsync(directory_fd)
            os.unlink(_TEMPORARY, dir_fd=directory_fd)
            raise _ActiveIdentityChanged
        os.fsync(directory_fd)
        os.unlink(_TEMPORARY, dir_fd=directory_fd)
        exchanged = False
    except BaseException:
        if exchanged:
            try:
                _rename_exchange_at(directory_fd, _TEMPORARY, _ACTIVE)
                exchanged = False
                os.fsync(directory_fd)
            except BaseException:
                pass
        try:
            os.unlink(_TEMPORARY, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise


def _create_active_backup_at(
    directory_fd: int,
    expected_active_identity: tuple[int, int],
) -> None:
    created = False
    try:
        try:
            os.stat(_BACKUP, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _reject()
        os.link(
            _ACTIVE,
            _BACKUP,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        created = True
        backup = os.stat(_BACKUP, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(backup.st_mode)
            or (backup.st_dev, backup.st_ino) != expected_active_identity
        ):
            raise _ActiveIdentityChanged
        os.fsync(directory_fd)
    except BaseException:
        if created:
            try:
                os.unlink(_BACKUP, dir_fd=directory_fd)
            except OSError:
                pass
        raise


def _restore_active_backup_at(directory_fd: int) -> None:
    backup = os.stat(_BACKUP, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(backup.st_mode):
        raise _reject()
    os.replace(
        _BACKUP,
        _ACTIVE,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
    )
    os.fsync(directory_fd)


def _discard_active_backup_at(directory_fd: int) -> None:
    os.fsync(directory_fd)
    os.unlink(_BACKUP, dir_fd=directory_fd)


def rollback_to_previous(
    *,
    workspace: Path,
    expected_current_sha: str,
) -> RollbackReceipt:
    """Restore candidate metadata while proving every SQLite byte is unchanged."""

    directory_fd: int | None = None
    backup_created = False
    try:
        if not isinstance(workspace, Path) or workspace.is_symlink():
            raise _reject()
        listed = os.stat(workspace, follow_symlinks=False)
        if not stat.S_ISDIR(listed.st_mode):
            raise _reject()
        resolved = workspace.resolve(strict=True)
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
        if resolved == temporary_root or not _is_within(resolved, temporary_root):
            raise _reject()
        directory_fd = os.open(
            workspace,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        opened = os.fstat(directory_fd)
        if (listed.st_dev, listed.st_ino) != (opened.st_dev, opened.st_ino):
            raise _reject()
        if _read_regular_at(directory_fd, ".v2-rollback-sandbox") != _MARKER:
            raise _reject()
        if type(expected_current_sha) is not str or _GIT_SHA.fullmatch(
            expected_current_sha
        ) is None:
            raise _reject()
        active_raw, active_identity = _read_regular_snapshot_at(directory_fd, _ACTIVE)
        active = _strict_object(active_raw)
        previous = _strict_object(_read_regular_at(directory_fd, _PREVIOUS))
        if active["git_sha"] != expected_current_sha:
            raise _reject()
        if previous["git_sha"] == active["git_sha"]:
            raise _reject()
        before = _sqlite_fingerprints(directory_fd)
        _create_active_backup_at(directory_fd, active_identity)
        backup_created = True
        _atomic_write_at(
            directory_fd,
            previous,
            expected_destination_identity=active_identity,
        )
        after = _sqlite_fingerprints(directory_fd)
        if before != after:
            raise _reject()
        _discard_active_backup_at(directory_fd)
        backup_created = False
    except BaseException as exc:
        if (
            backup_created
            and directory_fd is not None
            and isinstance(exc, _ActiveIdentityChanged)
        ):
            try:
                _discard_active_backup_at(directory_fd)
                backup_created = False
            except BaseException as discard_exc:
                raise _reject() from discard_exc
        if backup_created and directory_fd is not None:
            try:
                _restore_active_backup_at(directory_fd)
                backup_created = False
            except BaseException as restore_exc:
                raise _reject() from restore_exc
        if isinstance(exc, RollbackSafetyError):
            raise
        raise _reject() from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return RollbackReceipt(
        from_git_sha=active["git_sha"],
        to_git_sha=previous["git_sha"],
        sqlite_fingerprints_before=dict(before),
        sqlite_fingerprints_after=dict(after),
    )


__all__ = ["RollbackReceipt", "RollbackSafetyError", "rollback_to_previous"]
