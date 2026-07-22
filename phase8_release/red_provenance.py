"""Reproducible U/P/S/R/O provenance for focused Phase 8 REDs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Iterable, Literal

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
RootKind = Literal["detached_worktree", "temporary_index"]


class RedProvenanceError(RuntimeError):
    """RED provenance is malformed or cannot be reproduced."""


@dataclass(frozen=True, slots=True)
class ExecutionRootManifest:
    absolute_root: str
    root_kind: RootKind
    git_dir_identity: str
    head_commit: str
    staged_tree: str
    patch_paths: tuple[str, ...]
    python_executable: str
    python_version: str
    tool_versions: tuple[tuple[str, str], ...]
    env_names: tuple[str, ...]

    def __post_init__(self) -> None:
        _absolute_path(self.absolute_root, field="absolute_root")
        _absolute_path(self.git_dir_identity, field="git_dir_identity")
        _absolute_path(self.python_executable, field="python_executable")
        if self.root_kind not in ("detached_worktree", "temporary_index"):
            raise RedProvenanceError("root_kind is not closed")
        _object_id(self.head_commit, field="head_commit")
        _object_id(self.staged_tree, field="staged_tree")
        _test_paths(self.patch_paths)
        if not isinstance(self.python_version, str) or not self.python_version:
            raise RedProvenanceError("python_version must be a non-empty string")
        _name_value_pairs(self.tool_versions, field="tool_versions")
        _environment_names(self.env_names)

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class RedProvenance:
    unfixed_commit: str
    unfixed_tree: str
    test_patch_blob: str
    test_patch_sha256: str
    test_patch_paths: tuple[str, ...]
    staged_tree: str
    execution_root_manifest_sha256: str
    execution_root_absolute: str
    argv: tuple[str, ...]
    cwd: str
    env_name_allowlist: tuple[str, ...]
    python_version: str
    tool_versions: tuple[tuple[str, str], ...]
    exit_code: int
    duration_ns: int
    counts: tuple[tuple[str, int], ...]
    output_sha256: str
    output_bytes: int

    @classmethod
    def from_run(
        cls,
        *,
        execution_root_manifest: ExecutionRootManifest,
        test_patch_path: str | Path,
        argv: Iterable[str],
        cwd: str,
        env_name_allowlist: Iterable[str],
        exit_code: int,
        duration_ns: int,
        counts: Iterable[tuple[str, int]],
        output_path: str | Path,
    ) -> "RedProvenance":
        root = Path(execution_root_manifest.absolute_root)
        unfixed_tree = _git(root, "rev-parse", f"{execution_root_manifest.head_commit}^{{tree}}")
        patch = _regular_bytes(test_patch_path, field="test_patch_path")
        output = _regular_bytes(output_path, field="output_path")
        command = _strings(argv, field="argv")
        if not command:
            raise RedProvenanceError("argv cannot be empty")
        environment = _environment_names(tuple(env_name_allowlist))
        if environment != execution_root_manifest.env_names:
            raise RedProvenanceError("env_name_allowlist differs from execution root manifest")
        if Path(cwd).resolve() != root.resolve() or not Path(cwd).is_absolute():
            raise RedProvenanceError("cwd must equal the absolute execution root")
        if type(exit_code) is not int:
            raise RedProvenanceError("exit_code must be an exact integer")
        if type(duration_ns) is not int or duration_ns < 0:
            raise RedProvenanceError("duration_ns must be a non-negative integer")
        closed_counts = _counts(tuple(counts))
        return cls(
            unfixed_commit=execution_root_manifest.head_commit,
            unfixed_tree=unfixed_tree,
            test_patch_blob=_git_blob_id(patch),
            test_patch_sha256=hashlib.sha256(patch).hexdigest(),
            test_patch_paths=execution_root_manifest.patch_paths,
            staged_tree=execution_root_manifest.staged_tree,
            execution_root_manifest_sha256=execution_root_manifest.sha256(),
            execution_root_absolute=execution_root_manifest.absolute_root,
            argv=command,
            cwd=cwd,
            env_name_allowlist=environment,
            python_version=execution_root_manifest.python_version,
            tool_versions=execution_root_manifest.tool_versions,
            exit_code=exit_code,
            duration_ns=duration_ns,
            counts=closed_counts,
            output_sha256=hashlib.sha256(output).hexdigest(),
            output_bytes=len(output),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class RedReplayVerification:
    staged_tree: str
    changed_paths: tuple[str, ...]
    test_blobs: tuple[tuple[str, str], ...]


def verify_red_replay(
    repository_root: str | Path,
    provenance: RedProvenance,
    test_patch_path: str | Path,
) -> RedReplayVerification:
    repository = _repository(repository_root)
    patch_path = Path(test_patch_path)
    patch = _regular_bytes(patch_path, field="test_patch_path")
    if _git_blob_id(patch) != provenance.test_patch_blob:
        raise RedProvenanceError("test patch Git blob identity differs")
    if hashlib.sha256(patch).hexdigest() != provenance.test_patch_sha256:
        raise RedProvenanceError("test patch SHA-256 differs")
    actual_unfixed_tree = _git(repository, "rev-parse", f"{provenance.unfixed_commit}^{{tree}}")
    if actual_unfixed_tree != provenance.unfixed_tree:
        raise RedProvenanceError("unfixed commit/tree binding differs")

    repository_objects_value = Path(_git(repository, "rev-parse", "--git-path", "objects"))
    repository_objects = (
        repository_objects_value
        if repository_objects_value.is_absolute()
        else repository / repository_objects_value
    ).resolve()
    with tempfile.TemporaryDirectory(prefix="phase8-red-replay-") as directory:
        temporary = Path(directory)
        object_directory = temporary / "objects"
        (object_directory / "info").mkdir(parents=True)
        (object_directory / "pack").mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repository_objects),
                "GIT_INDEX_FILE": str(temporary / "index"),
                "GIT_OBJECT_DIRECTORY": str(object_directory),
                "GIT_OPTIONAL_LOCKS": "0",
            }
        )
        _git(repository, "read-tree", provenance.unfixed_commit, env=environment)
        _git(
            repository,
            "apply",
            "--cached",
            "--binary",
            "--unidiff-zero",
            "--whitespace=nowarn",
            str(patch_path.resolve()),
            env=environment,
        )
        staged_tree = _git(repository, "write-tree", env=environment)
        changed_output = _git(
            repository,
            "diff",
            "--cached",
            "--name-only",
            provenance.unfixed_commit,
            env=environment,
        )
        changed_paths = tuple(line for line in changed_output.splitlines() if line)
        if staged_tree != provenance.staged_tree:
            raise RedProvenanceError("replayed staged tree differs")
        if changed_paths != provenance.test_patch_paths:
            raise RedProvenanceError("replayed changed paths differ")
        test_blobs = tuple(
            (
                path,
                _git(repository, "rev-parse", f"{staged_tree}:{path}", env=environment),
            )
            for path in changed_paths
        )
    return RedReplayVerification(
        staged_tree=staged_tree,
        changed_paths=changed_paths,
        test_blobs=test_blobs,
    )


def _repository(value: str | Path) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RedProvenanceError("repository root cannot be resolved") from exc
    if path.is_symlink() or not resolved.is_dir():
        raise RedProvenanceError("repository root must be a non-symlink directory")
    if _git(resolved, "rev-parse", "--is-inside-work-tree") != "true":
        raise RedProvenanceError("repository root is not a Git worktree")
    return resolved


def _regular_bytes(value: str | Path, *, field: str) -> bytes:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RedProvenanceError(f"{field} cannot be resolved") from exc
    if path.is_symlink() or not resolved.is_file():
        raise RedProvenanceError(f"{field} must be a regular non-symlink file")
    try:
        return resolved.read_bytes()
    except OSError as exc:
        raise RedProvenanceError(f"{field} cannot be read") from exc


def _git_blob_id(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


def _object_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _HEX40.fullmatch(value) is None:
        raise RedProvenanceError(f"{field} must be a full lowercase Git object ID")
    return value


def _absolute_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise RedProvenanceError(f"{field} must be an absolute path")
    return value


def _closed_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RedProvenanceError(f"{field} must contain non-empty strings")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RedProvenanceError(f"{field} contains an unsafe path")
    return path.as_posix()


def _test_paths(values: Iterable[str]) -> tuple[str, ...]:
    paths = tuple(_closed_path(value, field="patch_paths") for value in values)
    if not paths or len(paths) != len(set(paths)) or tuple(sorted(paths)) != paths:
        raise RedProvenanceError("patch_paths must be a sorted, unique, non-empty tuple")
    if any(not path.startswith("tests/") for path in paths):
        raise RedProvenanceError("RED patch may change only tests/ paths")
    return paths


def _strings(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise RedProvenanceError(f"{field} must contain non-empty strings")
    return result


def _environment_names(values: Iterable[str]) -> tuple[str, ...]:
    result = tuple(values)
    if (
        any(not isinstance(value, str) or _ENV_NAME.fullmatch(value) is None for value in result)
        or len(set(result)) != len(result)
    ):
        raise RedProvenanceError("environment names are invalid or duplicated")
    return result


def _name_value_pairs(
    values: Iterable[tuple[str, str]],
    *,
    field: str,
) -> tuple[tuple[str, str], ...]:
    result = tuple(values)
    if any(
        not isinstance(item, tuple)
        or len(item) != 2
        or not all(isinstance(value, str) and value for value in item)
        for item in result
    ):
        raise RedProvenanceError(f"{field} must contain non-empty string pairs")
    if len({name for name, _ in result}) != len(result):
        raise RedProvenanceError(f"{field} contains duplicate names")
    return result


def _counts(values: Iterable[tuple[str, int]]) -> tuple[tuple[str, int], ...]:
    result = tuple(values)
    if any(
        not isinstance(item, tuple)
        or len(item) != 2
        or not isinstance(item[0], str)
        or not item[0]
        or type(item[1]) is not int
        or item[1] < 0
        for item in result
    ):
        raise RedProvenanceError("counts must contain name/non-negative integer pairs")
    if len({name for name, _ in result}) != len(result):
        raise RedProvenanceError("counts contains duplicate names")
    return result


def _git(
    repository: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RedProvenanceError(f"Git verification failed: {' '.join(args)}") from exc
    return completed.stdout.strip()


__all__ = (
    "ExecutionRootManifest",
    "RedProvenance",
    "RedProvenanceError",
    "RedReplayVerification",
    "verify_red_replay",
)
