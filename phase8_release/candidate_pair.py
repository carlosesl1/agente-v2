"""Immutable functional/evidence candidate-pair verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Iterable

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class CandidatePairError(RuntimeError):
    """A functional/evidence pair violates its immutable contract."""


@dataclass(frozen=True, slots=True)
class CandidatePair:
    functional_commit: str
    functional_tree: str
    evidence_commit: str
    evidence_tree: str
    evidence_paths: tuple[str, ...]
    required_test_blobs: tuple[tuple[str, str], ...]

    @classmethod
    def verify(
        cls,
        *,
        repository_root: str | Path,
        functional_commit: str,
        functional_tree: str,
        evidence_commit: str,
        evidence_tree: str,
        evidence_allowed_paths: Iterable[str],
        required_test_blobs: Iterable[tuple[str, str]],
    ) -> "CandidatePair":
        repository = _repository(repository_root)
        for field, value in (
            ("functional_commit", functional_commit),
            ("functional_tree", functional_tree),
            ("evidence_commit", evidence_commit),
            ("evidence_tree", evidence_tree),
        ):
            if not isinstance(value, str) or _HEX40.fullmatch(value) is None:
                raise CandidatePairError(f"{field} must be a full lowercase Git object ID")

        actual_functional_tree = _git(repository, "rev-parse", f"{functional_commit}^{{tree}}")
        actual_evidence_tree = _git(repository, "rev-parse", f"{evidence_commit}^{{tree}}")
        if actual_functional_tree != functional_tree:
            raise CandidatePairError("functional tree does not match functional commit")
        if actual_evidence_tree != evidence_tree:
            raise CandidatePairError("evidence tree does not match evidence commit")

        ancestry = _git(repository, "rev-list", "--parents", "-n", "1", evidence_commit).split()
        if ancestry != [evidence_commit, functional_commit]:
            raise CandidatePairError("evidence commit must be the direct child of functional commit")

        allowed = _closed_paths(evidence_allowed_paths, field="evidence_allowed_paths")
        evidence_status = _git_lines(
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            "-M",
            "-C",
            functional_commit,
            evidence_commit,
        )
        actual_paths: list[str] = []
        for row in evidence_status:
            parts = row.split("\t")
            status = parts[0]
            if status not in {"A", "M"} or len(parts) != 2:
                raise CandidatePairError(f"evidence child has forbidden change status: {row}")
            actual_paths.append(_closed_path(parts[1], field="evidence diff path"))
        if tuple(sorted(actual_paths)) != allowed:
            raise CandidatePairError("evidence child path universe differs from its exact allowlist")

        required = _closed_test_blobs(required_test_blobs)
        for path, expected_blob in required:
            try:
                actual_blob = _git(repository, "rev-parse", f"{functional_tree}:{path}")
            except CandidatePairError as exc:
                raise CandidatePairError(f"functional candidate is missing RED test path: {path}") from exc
            if actual_blob != expected_blob:
                raise CandidatePairError(f"functional candidate changed RED test bytes: {path}")

        return cls(
            functional_commit=functional_commit,
            functional_tree=functional_tree,
            evidence_commit=evidence_commit,
            evidence_tree=evidence_tree,
            evidence_paths=allowed,
            required_test_blobs=required,
        )


def _repository(value: str | Path) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CandidatePairError("repository root cannot be resolved") from exc
    if path.is_symlink() or not resolved.is_dir():
        raise CandidatePairError("repository root must be a non-symlink directory")
    if _git(resolved, "rev-parse", "--is-inside-work-tree") != "true":
        raise CandidatePairError("repository root is not a Git worktree")
    return resolved


def _closed_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidatePairError(f"{field} must contain non-empty strings")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CandidatePairError(f"{field} contains an unsafe path: {value!r}")
    return path.as_posix()


def _closed_paths(values: Iterable[str], *, field: str) -> tuple[str, ...]:
    paths = tuple(sorted(_closed_path(value, field=field) for value in values))
    if len(paths) != len(set(paths)):
        raise CandidatePairError(f"{field} contains duplicates")
    return paths


def _closed_test_blobs(
    values: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise CandidatePairError("required_test_blobs must contain exact pairs")
        path = _closed_path(item[0], field="required_test_blobs")
        blob = item[1]
        if not path.startswith("tests/") or not isinstance(blob, str) or _HEX40.fullmatch(blob) is None:
            raise CandidatePairError("required_test_blobs contains an invalid test/blob identity")
        result.append((path, blob))
    result.sort()
    if len({path for path, _ in result}) != len(result):
        raise CandidatePairError("required_test_blobs contains duplicate paths")
    return tuple(result)


def _git(repository: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CandidatePairError(f"Git verification failed: {' '.join(args)}") from exc
    return completed.stdout.strip()


def _git_lines(repository: Path, *args: str) -> tuple[str, ...]:
    output = _git(repository, *args)
    return tuple(output.splitlines()) if output else ()


__all__ = ("CandidatePair", "CandidatePairError")
