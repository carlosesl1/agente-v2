"""Fail-closed runtime identity verification without Git or Docker socket access."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE_REF = re.compile(
    r"(?P<repository>[a-z0-9][a-z0-9._:/-]*)@(?P<digest>sha256:[0-9a-f]{64})\Z"
)
_REVISION_LABEL = "org.opencontainers.image.revision"
_SCHEMA = "v2-runtime-image-metadata-v1"
_MAX_METADATA_BYTES = 65_536


class RuntimeIdentityError(ValueError):
    """Closed diagnostic for all malformed or divergent runtime identities."""

    def __init__(self) -> None:
        super().__init__("runtime identity rejected")


@dataclass(frozen=True, slots=True)
class RuntimeImageMetadata:
    labels: Mapping[str, str]
    repo_digests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeIdentity:
    git_sha: str
    image_ref: str
    image_digest: str


def _reject() -> RuntimeIdentityError:
    return RuntimeIdentityError()


def _strict_json(raw: bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise _reject()
            value[key] = item
        return value

    try:
        return json.loads(raw, object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError, RuntimeIdentityError) as exc:
        raise _reject() from exc


def load_runtime_image_metadata(path: Path) -> RuntimeImageMetadata:
    """Load one closed metadata document supplied by the deployment controller."""

    descriptor = -1
    try:
        if not isinstance(path, Path):
            raise _reject()
        listed = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(listed.st_mode):
            raise _reject()
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (listed.st_dev, listed.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise _reject()
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(_MAX_METADATA_BYTES + 1)
        if len(raw) > _MAX_METADATA_BYTES:
            raise _reject()
        payload = _strict_json(raw)
    except (OSError, UnicodeError, RuntimeIdentityError) as exc:
        raise _reject() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if type(payload) is not dict or set(payload) != {
        "schema",
        "labels",
        "repo_digests",
    }:
        raise _reject()
    if payload["schema"] != _SCHEMA:
        raise _reject()
    labels = payload["labels"]
    digests = payload["repo_digests"]
    if type(labels) is not dict or any(
        type(key) is not str or type(value) is not str
        for key, value in labels.items()
    ):
        raise _reject()
    if type(digests) is not list or any(type(value) is not str for value in digests):
        raise _reject()
    return RuntimeImageMetadata(labels=dict(labels), repo_digests=tuple(digests))


def verify_runtime_identity(
    *,
    expected_git_sha: str,
    expected_image_ref: str,
    expected_image_digest: str,
    metadata: RuntimeImageMetadata,
) -> VerifiedRuntimeIdentity:
    """Require exact source revision and immutable repository digest identity."""

    image_match = (
        _IMAGE_REF.fullmatch(expected_image_ref)
        if type(expected_image_ref) is str
        else None
    )
    if (
        type(expected_git_sha) is not str
        or _GIT_SHA.fullmatch(expected_git_sha) is None
        or type(expected_image_digest) is not str
        or _DIGEST.fullmatch(expected_image_digest) is None
        or image_match is None
        or image_match.group("digest") != expected_image_digest
        or type(metadata) is not RuntimeImageMetadata
        or type(metadata.labels) not in {dict}
        or type(metadata.repo_digests) is not tuple
        or metadata.labels.get(_REVISION_LABEL) != expected_git_sha
        or expected_image_ref not in metadata.repo_digests
        or len(set(metadata.repo_digests)) != len(metadata.repo_digests)
    ):
        raise _reject()
    for candidate in metadata.repo_digests:
        candidate_match = (
            _IMAGE_REF.fullmatch(candidate) if type(candidate) is str else None
        )
        if (
            candidate_match is None
            or candidate_match.group("digest") != expected_image_digest
        ):
            raise _reject()
    return VerifiedRuntimeIdentity(
        git_sha=expected_git_sha,
        image_ref=expected_image_ref,
        image_digest=expected_image_digest,
    )


def verify_runtime_identity_file(
    *,
    expected_git_sha: str,
    expected_image_ref: str,
    expected_image_digest: str,
    metadata_path: Path,
) -> VerifiedRuntimeIdentity:
    return verify_runtime_identity(
        expected_git_sha=expected_git_sha,
        expected_image_ref=expected_image_ref,
        expected_image_digest=expected_image_digest,
        metadata=load_runtime_image_metadata(metadata_path),
    )


__all__ = [
    "RuntimeIdentityError",
    "RuntimeImageMetadata",
    "VerifiedRuntimeIdentity",
    "load_runtime_image_metadata",
    "verify_runtime_identity",
    "verify_runtime_identity_file",
]
