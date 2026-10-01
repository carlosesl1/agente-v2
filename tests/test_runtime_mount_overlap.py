"""Writable ownership uses directory ancestry, not string equality alone."""

from pathlib import Path

import pytest

from scripts.runtime_authority import AuthorityError, load_manifest
from test_runtime_authority import _container_for, _write_manifest, minimal_manifest


def _mount(source: str, *, read_only: bool = False) -> dict[str, object]:
    return {"source": source, "destination": "/data", "read_only": read_only}


@pytest.mark.parametrize(
    ("ga_path", "test_path"),
    [
        ("/srv/ga", "/srv/ga/ops/trace"),
        ("/srv/ga/ops/trace", "/srv/ga"),
        ("/srv/ga", "/srv/ga"),
    ],
)
def test_cross_component_writable_ancestor_is_rejected(
    tmp_path: Path, ga_path: str, test_path: str
) -> None:
    manifest = minimal_manifest()
    _container_for(manifest, "ga", "worker")["mounts"] = [_mount(ga_path)]
    _container_for(manifest, "test_contact", "worker")["mounts"] = [_mount(test_path)]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="writable mount source"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("test_path", "read_only"),
    [("/srv/ga-other", False), ("/srv/test", False), ("/srv/ga/trace", True)],
)
def test_disjoint_or_readonly_cross_component_mounts_remain_valid(
    tmp_path: Path, test_path: str, read_only: bool
) -> None:
    manifest = minimal_manifest()
    _container_for(manifest, "ga", "worker")["mounts"] = [_mount("/srv/ga")]
    _container_for(manifest, "test_contact", "worker")["mounts"] = [
        _mount(test_path, read_only=read_only)
    ]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    assert load_manifest(path) == manifest


def test_same_component_may_share_parent_and_child_mounts(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    _container_for(manifest, "ga", "api")["mounts"] = [_mount("/srv/ga")]
    _container_for(manifest, "ga", "worker")["mounts"] = [_mount("/srv/ga/trace")]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    assert load_manifest(path) == manifest
