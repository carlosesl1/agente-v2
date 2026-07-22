"""Private content-addressed evidence store with fail-closed recovery."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Iterator

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STAGE = re.compile(r"^[0-9a-f]{32}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class ManualReviewError(RuntimeError):
    """Evidence state is divergent or structurally unsafe."""


@dataclass(frozen=True, slots=True)
class EvidenceObject:
    path: Path
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    removed: tuple[str, ...]
    retained: tuple[str, ...]


class EvidenceArtifactStore:
    """Publish immutable SHA-256 objects under a private three-entry root."""

    def __init__(self, root: str | Path) -> None:
        if _NOFOLLOW == 0 or _DIRECTORY == 0:
            raise ManualReviewError("O_NOFOLLOW and O_DIRECTORY are required")
        self.root = Path(root)
        self._initialize()

    def publish(
        self,
        payload: bytes,
        expected_sha256: str | None = None,
    ) -> EvidenceObject:
        if type(payload) is not bytes:
            raise TypeError("payload must be exact bytes")
        digest = hashlib.sha256(payload).hexdigest()
        if expected_sha256 is not None:
            _digest(expected_sha256)
            if expected_sha256 != digest:
                raise ManualReviewError("payload differs from expected SHA-256")

        stage_name: str | None = None
        stage_fd: int | None = None
        owner_fd: int | None = None
        temporary_fd: int | None = None
        published = False
        result: EvidenceObject | None = None
        try:
            with self._coord_lock():
                objects_fd = self._open_directory(self.root / "objects")
                try:
                    existing = self._verify_existing(objects_fd, digest, payload, missing_ok=True)
                    if existing is not None:
                        return existing
                finally:
                    os.close(objects_fd)

                staging_fd = self._open_directory(self.root / ".staging")
                try:
                    for _ in range(128):
                        candidate = secrets.token_hex(16)
                        try:
                            os.mkdir(candidate, mode=0o700, dir_fd=staging_fd)
                        except FileExistsError:
                            continue
                        stage_name = candidate
                        os.fsync(staging_fd)
                        break
                    if stage_name is None:
                        raise ManualReviewError("could not allocate a unique staging prefix")
                    stage_fd = os.open(
                        stage_name,
                        os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                        dir_fd=staging_fd,
                    )
                    self._verify_directory_fd(
                        stage_fd,
                        expected_mode=0o700,
                        label="staging prefix",
                    )
                    owner_fd = os.open(
                        "owner.lock",
                        os.O_CREAT | os.O_EXCL | os.O_RDWR | _NOFOLLOW,
                        0o600,
                        dir_fd=stage_fd,
                    )
                    os.fchmod(owner_fd, 0o600)
                    self._verify_regular_fd(
                        owner_fd,
                        expected_modes={0o600},
                        label="owner.lock",
                    )
                    os.fsync(owner_fd)
                    os.fsync(stage_fd)
                    fcntl.flock(owner_fd, fcntl.LOCK_EX)
                    temporary_fd = os.open(
                        "object.tmp",
                        os.O_CREAT | os.O_EXCL | os.O_RDWR | _NOFOLLOW,
                        0o600,
                        dir_fd=stage_fd,
                    )
                    os.fchmod(temporary_fd, 0o600)
                    os.fsync(stage_fd)
                finally:
                    os.close(staging_fd)

            assert stage_name is not None
            assert stage_fd is not None
            assert owner_fd is not None
            assert temporary_fd is not None
            _write_all(temporary_fd, payload)
            os.fsync(temporary_fd)
            os.close(temporary_fd)
            temporary_fd = None

            reopened = os.open("object.tmp", os.O_RDONLY | _NOFOLLOW, dir_fd=stage_fd)
            try:
                self._verify_regular_fd(
                    reopened,
                    expected_modes={0o600},
                    label="object.tmp before chmod",
                )
                if _read_all(reopened) != payload:
                    raise ManualReviewError("staged object differs after first fsync")
                os.fchmod(reopened, 0o400)
                os.fsync(reopened)
            finally:
                os.close(reopened)

            reopened = os.open("object.tmp", os.O_RDONLY | _NOFOLLOW, dir_fd=stage_fd)
            try:
                self._verify_regular_fd(
                    reopened,
                    expected_modes={0o400},
                    label="object.tmp after chmod",
                )
                verified = _read_all(reopened)
                if len(verified) != len(payload) or hashlib.sha256(verified).hexdigest() != digest:
                    raise ManualReviewError("staged object differs after chmod/fsync/rehash")
            finally:
                os.close(reopened)

            objects_fd = self._open_directory(self.root / "objects")
            try:
                try:
                    os.link(
                        "object.tmp",
                        digest,
                        src_dir_fd=stage_fd,
                        dst_dir_fd=objects_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    self._verify_existing(objects_fd, digest, payload, missing_ok=False)
                os.fsync(objects_fd)
                result = self._verify_existing(objects_fd, digest, payload, missing_ok=False)
                assert result is not None
            finally:
                os.close(objects_fd)
            published = True
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if owner_fd is not None:
                fcntl.flock(owner_fd, fcntl.LOCK_UN)
                os.close(owner_fd)
            if stage_fd is not None:
                os.close(stage_fd)

        if published:
            assert stage_name is not None
            self._cleanup_published_stage(stage_name)
        assert result is not None
        return result

    def recover(self) -> RecoveryReport:
        removed: list[str] = []
        retained: list[str] = []
        with self._coord_lock():
            staging_fd = self._open_directory(self.root / ".staging")
            try:
                for name in sorted(os.listdir(staging_fd)):
                    if _STAGE.fullmatch(name) is None:
                        raise ManualReviewError(f"unknown staging prefix: {name}")
                    outcome = self._recover_stage_locked(staging_fd, name)
                    if outcome == "removed":
                        removed.append(name)
                    else:
                        retained.append(name)
            finally:
                os.close(staging_fd)
        return RecoveryReport(removed=tuple(removed), retained=tuple(retained))

    def _initialize(self) -> None:
        parent = self.root.parent
        try:
            parent_resolved = parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ManualReviewError("evidence-store parent cannot be resolved") from exc
        if parent.is_symlink() or not parent_resolved.is_dir():
            raise ManualReviewError("evidence-store parent must be a non-symlink directory")
        if self.root.exists() or self.root.is_symlink():
            self._verify_directory_path(self.root, expected_mode=0o700, label="store root")
        else:
            try:
                self.root.mkdir(mode=0o700)
                self.root.chmod(0o700)
            except OSError as exc:
                raise ManualReviewError("evidence-store root cannot be created") from exc
            _fsync_directory(parent_resolved)

        self._ensure_directory(self.root / ".staging", mode=0o700)
        self._ensure_directory(self.root / "objects", mode=0o700)
        self._ensure_lock(self.root / "coord.lock")
        entries = {path.name for path in self.root.iterdir()}
        if entries != {"coord.lock", ".staging", "objects"}:
            raise ManualReviewError("evidence-store root has an unknown member")
        _fsync_directory(self.root)

    def _ensure_directory(self, path: Path, *, mode: int) -> None:
        if path.exists() or path.is_symlink():
            self._verify_directory_path(path, expected_mode=mode, label=path.name)
            return
        try:
            path.mkdir(mode=mode)
            path.chmod(mode)
        except OSError as exc:
            raise ManualReviewError(f"cannot create directory: {path.name}") from exc
        _fsync_directory(path.parent)

    def _ensure_lock(self, path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDWR | _NOFOLLOW)
            created = False
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | _NOFOLLOW,
                    0o600,
                )
                created = True
            except OSError as exc:
                raise ManualReviewError("cannot create coord.lock") from exc
        except OSError as exc:
            raise ManualReviewError("cannot open coord.lock") from exc
        try:
            if created:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
            self._verify_regular_fd(descriptor, expected_modes={0o600}, label="coord.lock")
        finally:
            os.close(descriptor)
        if created:
            _fsync_directory(path.parent)

    @contextmanager
    def _coord_lock(self) -> Iterator[None]:
        try:
            descriptor = os.open(self.root / "coord.lock", os.O_RDWR | _NOFOLLOW)
        except OSError as exc:
            raise ManualReviewError("cannot open coord.lock") from exc
        try:
            self._verify_regular_fd(descriptor, expected_modes={0o600}, label="coord.lock")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _cleanup_published_stage(self, stage_name: str) -> None:
        with self._coord_lock():
            staging_fd = self._open_directory(self.root / ".staging")
            try:
                if stage_name not in os.listdir(staging_fd):
                    return
                outcome = self._recover_stage_locked(staging_fd, stage_name)
                if outcome != "removed":
                    raise ManualReviewError("published staging prefix remained locked")
            finally:
                os.close(staging_fd)

    def _recover_stage_locked(self, staging_fd: int, name: str) -> str:
        try:
            stage_fd = os.open(
                name,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                dir_fd=staging_fd,
            )
        except OSError as exc:
            raise ManualReviewError(f"cannot open staging prefix: {name}") from exc
        owner_fd: int | None = None
        try:
            self._verify_directory_fd(stage_fd, expected_mode=0o700, label="staging prefix")
            members = frozenset(os.listdir(stage_fd))
            if members == frozenset():
                os.close(stage_fd)
                stage_fd = -1
                os.rmdir(name, dir_fd=staging_fd)
                os.fsync(staging_fd)
                return "removed"
            if members not in (
                frozenset({"owner.lock"}),
                frozenset({"owner.lock", "object.tmp"}),
            ):
                raise ManualReviewError(f"unknown staging members in {name}: {sorted(members)}")
            try:
                owner_fd = os.open("owner.lock", os.O_RDWR | _NOFOLLOW, dir_fd=stage_fd)
                self._verify_regular_fd(owner_fd, expected_modes={0o600}, label="owner.lock")
                fcntl.flock(owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if owner_fd is not None:
                    os.close(owner_fd)
                    owner_fd = None
                return "retained"
            except OSError as exc:
                raise ManualReviewError(f"unsafe owner.lock in staging prefix: {name}") from exc

            if "object.tmp" in members:
                try:
                    temporary_fd = os.open("object.tmp", os.O_RDONLY | _NOFOLLOW, dir_fd=stage_fd)
                except OSError as exc:
                    raise ManualReviewError(f"unsafe object.tmp in staging prefix: {name}") from exc
                try:
                    self._verify_regular_fd(
                        temporary_fd,
                        expected_modes={0o600, 0o400},
                        label="object.tmp",
                    )
                finally:
                    os.close(temporary_fd)
                os.unlink("object.tmp", dir_fd=stage_fd)
            os.unlink("owner.lock", dir_fd=stage_fd)
            os.fsync(stage_fd)
            fcntl.flock(owner_fd, fcntl.LOCK_UN)
            os.close(owner_fd)
            owner_fd = None
            os.close(stage_fd)
            stage_fd = -1
            os.rmdir(name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            return "removed"
        finally:
            if owner_fd is not None:
                try:
                    fcntl.flock(owner_fd, fcntl.LOCK_UN)
                finally:
                    os.close(owner_fd)
            if stage_fd >= 0:
                os.close(stage_fd)

    def _verify_existing(
        self,
        objects_fd: int,
        digest: str,
        expected: bytes,
        *,
        missing_ok: bool,
    ) -> EvidenceObject | None:
        _digest(digest)
        try:
            descriptor = os.open(digest, os.O_RDONLY | _NOFOLLOW, dir_fd=objects_fd)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ManualReviewError("published evidence object disappeared")
        except OSError as exc:
            raise ManualReviewError("published evidence object is unsafe") from exc
        try:
            self._verify_regular_fd(descriptor, expected_modes={0o400}, label="evidence object")
            actual = _read_all(descriptor)
        finally:
            os.close(descriptor)
        if len(actual) != len(expected) or hashlib.sha256(actual).hexdigest() != digest or actual != expected:
            raise ManualReviewError("existing evidence object is divergent")
        return EvidenceObject(path=self.root / "objects" / digest, sha256=digest, bytes=len(actual))

    def _open_directory(self, path: Path) -> int:
        try:
            descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        except OSError as exc:
            raise ManualReviewError(f"cannot open directory: {path.name}") from exc
        try:
            self._verify_directory_fd(descriptor, expected_mode=0o700, label=path.name)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _verify_directory_path(self, path: Path, *, expected_mode: int, label: str) -> None:
        if path.is_symlink():
            raise ManualReviewError(f"{label} must not be a symlink")
        descriptor = self._open_directory_unchecked(path, label=label)
        try:
            self._verify_directory_fd(descriptor, expected_mode=expected_mode, label=label)
        finally:
            os.close(descriptor)

    @staticmethod
    def _open_directory_unchecked(path: Path, *, label: str) -> int:
        try:
            return os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        except OSError as exc:
            raise ManualReviewError(f"cannot open {label}") from exc

    @staticmethod
    def _verify_directory_fd(descriptor: int, *, expected_mode: int, label: str) -> None:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_uid != os.geteuid()
        ):
            raise ManualReviewError(f"{label} has unsafe owner, mode, or type")

    @staticmethod
    def _verify_regular_fd(
        descriptor: int,
        *,
        expected_modes: set[int],
        label: str,
    ) -> None:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) not in expected_modes
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink < 1
        ):
            raise ManualReviewError(f"{label} has unsafe owner, mode, or type")


def _digest(value: object) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ManualReviewError("expected SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ManualReviewError("short write to staged evidence object")
        view = view[written:]


def _read_all(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
    except OSError as exc:
        raise ManualReviewError(f"cannot open directory for fsync: {path}") from exc
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = (
    "EvidenceArtifactStore",
    "EvidenceObject",
    "ManualReviewError",
    "RecoveryReport",
)
