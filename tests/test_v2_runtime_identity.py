from __future__ import annotations

import json
from pathlib import Path

import pytest

import deploy.verify_runtime_identity as identity_module
from deploy.verify_runtime_identity import (
    RuntimeIdentityError,
    RuntimeImageMetadata,
    load_runtime_image_metadata,
    verify_runtime_identity,
    verify_runtime_identity_file,
)


SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
IMAGE_REF = f"ghcr.io/carlosesl1/agente-v2@{DIGEST}"


def _metadata() -> RuntimeImageMetadata:
    return RuntimeImageMetadata(
        labels={"org.opencontainers.image.revision": SHA},
        repo_digests=(IMAGE_REF,),
    )


def test_exact_revision_and_repo_digest_are_accepted() -> None:
    verified = verify_runtime_identity(
        expected_git_sha=SHA,
        expected_image_ref=IMAGE_REF,
        expected_image_digest=DIGEST,
        metadata=_metadata(),
    )

    assert verified.git_sha == SHA
    assert verified.image_ref == IMAGE_REF
    assert verified.image_digest == DIGEST


@pytest.mark.parametrize(
    ("git_sha", "image_ref", "digest", "metadata"),
    [
        ("c" * 40, IMAGE_REF, DIGEST, _metadata()),
        (SHA, "ghcr.io/carlosesl1/agente-v2:latest", DIGEST, _metadata()),
        (SHA, "ghcr.io/carlosesl1/agente-v2:sha-a", DIGEST, _metadata()),
        (SHA, IMAGE_REF, "", _metadata()),
        (
            SHA,
            IMAGE_REF,
            DIGEST,
            RuntimeImageMetadata(
                labels={"org.opencontainers.image.revision": SHA},
                repo_digests=(),
            ),
        ),
        (
            SHA,
            IMAGE_REF,
            DIGEST,
            RuntimeImageMetadata(
                labels={"org.opencontainers.image.revision": SHA},
                repo_digests=(
                    "ghcr.io/carlosesl1/agente-v2@sha256:" + "d" * 64,
                ),
            ),
        ),
    ],
)
def test_identity_mismatch_mutable_ref_or_absent_digest_fails_closed(
    git_sha: str,
    image_ref: str,
    digest: str,
    metadata: RuntimeImageMetadata,
) -> None:
    with pytest.raises(RuntimeIdentityError, match="runtime identity rejected"):
        verify_runtime_identity(
            expected_git_sha=git_sha,
            expected_image_ref=image_ref,
            expected_image_digest=digest,
            metadata=metadata,
        )


def test_metadata_loader_has_closed_schema_and_rejects_duplicate_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-identity.json"
    path.write_text(
        json.dumps(
            {
                "schema": "v2-runtime-image-metadata-v1",
                "labels": {"org.opencontainers.image.revision": SHA},
                "repo_digests": [IMAGE_REF],
            }
        ),
        encoding="utf-8",
    )

    assert load_runtime_image_metadata(path) == _metadata()

    path.write_text(
        '{"schema":"v2-runtime-image-metadata-v1",'
        '"schema":"v2-runtime-image-metadata-v1",'
        '"labels":{},"repo_digests":[]}',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeIdentityError, match="runtime identity rejected"):
        load_runtime_image_metadata(path)


def test_invalid_utf8_is_normalized_without_echoing_raw_metadata(tmp_path: Path) -> None:
    sentinel = "PRIVATE_RUNTIME_SENTINEL"
    path = tmp_path / "runtime-identity.json"
    path.write_bytes(b'{"private":"' + sentinel.encode() + b'","bad":"\xff"}')

    with pytest.raises(RuntimeIdentityError) as captured:
        load_runtime_image_metadata(path)

    assert str(captured.value) == "runtime identity rejected"
    assert sentinel not in repr(captured.value)


def test_metadata_symlink_swap_between_stat_and_read_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runtime-identity.json"
    replacement = tmp_path / "replacement.json"
    path.write_text(
        json.dumps(
            {
                "schema": "v2-runtime-image-metadata-v1",
                "labels": {"org.opencontainers.image.revision": "c" * 40},
                "repo_digests": [f"ghcr.io/carlosesl1/agente-v2@sha256:{'d' * 64}"],
            }
        ),
        encoding="utf-8",
    )
    replacement.write_text(
        json.dumps(
            {
                "schema": "v2-runtime-image-metadata-v1",
                "labels": {"org.opencontainers.image.revision": SHA},
                "repo_digests": [IMAGE_REF],
            }
        ),
        encoding="utf-8",
    )
    original_is_file = Path.is_file
    original_stat = identity_module.os.stat
    swapped = False

    def swap_after_stat(candidate, *args, **kwargs):
        nonlocal swapped
        result = original_stat(candidate, *args, **kwargs)
        if (
            Path(candidate) == path
            and kwargs.get("follow_symlinks") is False
            and not swapped
        ):
            swapped = True
            path.unlink()
            path.symlink_to(replacement)
        return result

    def swap_after_check(candidate: Path) -> bool:
        nonlocal swapped
        result = original_is_file(candidate)
        if candidate == path and not swapped:
            swapped = True
            candidate.unlink()
            candidate.symlink_to(replacement)
        return result

    monkeypatch.setattr(identity_module.os, "stat", swap_after_stat)
    monkeypatch.setattr(Path, "is_file", swap_after_check)

    with pytest.raises(RuntimeIdentityError, match="runtime identity rejected"):
        verify_runtime_identity_file(
            expected_git_sha=SHA,
            expected_image_ref=IMAGE_REF,
            expected_image_digest=DIGEST,
            metadata_path=path,
        )
    assert swapped is True


def test_conflicting_extra_repo_digest_fails_closed() -> None:
    with pytest.raises(RuntimeIdentityError, match="runtime identity rejected"):
        verify_runtime_identity(
            expected_git_sha=SHA,
            expected_image_ref=IMAGE_REF,
            expected_image_digest=DIGEST,
            metadata=RuntimeImageMetadata(
                labels={"org.opencontainers.image.revision": SHA},
                repo_digests=(
                    IMAGE_REF,
                    f"ghcr.io/carlosesl1/agente-v2@sha256:{'d' * 64}",
                ),
            ),
        )


def test_error_text_never_echoes_metadata_or_expected_values() -> None:
    private = "PRIVATE_METADATA_SENTINEL"
    with pytest.raises(RuntimeIdentityError) as captured:
        verify_runtime_identity(
            expected_git_sha=SHA,
            expected_image_ref=IMAGE_REF,
            expected_image_digest=DIGEST,
            metadata=RuntimeImageMetadata(
                labels={
                    "org.opencontainers.image.revision": "c" * 40,
                    "private": private,
                },
                repo_digests=(private,),
            ),
        )

    assert str(captured.value) == "runtime identity rejected"
    assert private not in repr(captured.value)
    assert IMAGE_REF not in repr(captured.value)


def test_verifier_has_no_git_docker_socket_or_subprocess_dependency() -> None:
    source = Path(__file__).resolve().parents[1] / "deploy/verify_runtime_identity.py"
    text = source.read_text(encoding="utf-8")
    assert "subprocess" not in text
    assert ".git" not in text
    assert "docker.sock" not in text
