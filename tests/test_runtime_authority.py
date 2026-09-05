"""Contract tests for the versioned, fail-closed runtime authority verifier."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import runtime_authority as authority
from scripts.runtime_authority import AuthorityError, load_manifest, render_markdown


COMPONENT_NAMES = ("ga", "test_contact", "ops")
_COMPONENT_HEX = {"ga": "1", "test_contact": "2", "ops": "3"}


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file_bytes(name: str) -> bytes:
    return f"print('synthetic {name}')\n".encode()


def _pointer_bytes(name: str) -> bytes:
    return f"agente-v2:{name}:synthetic\n".encode()


def _component(name: str) -> dict[str, Any]:
    marker = _COMPONENT_HEX[name]
    slug = name.replace("_", "-")
    file_bytes = _file_bytes(name)
    port = {"ga": 18081, "test_contact": 18082, "ops": 18083}[name]
    return {
        "source": {"commit": marker * 40, "tree": marker.upper() * 40},
        "image": {
            "ref": f"registry.invalid/agente-v2-{slug}@sha256:{marker * 64}",
            "id": f"sha256:{marker * 64}",
            "revision": marker * 40,
        },
        "container": {
            "name": f"agente-v2-{slug}",
            "compose_project": "agente-v2",
            "compose_config": "/srv/agente-v2/compose.yml",
            "compose_service": slug,
            "status": "running",
            "health": "healthy",
            "user": "65532:65532",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mounts": [
                {
                    "source": f"/srv/agente-v2/{slug}/config",
                    "destination": "/app/config",
                    "read_only": True,
                }
            ],
        },
        "traefik": {
            "labels": {
                "traefik.enable": "true",
                f"traefik.http.routers.{slug}.priority": str(
                    {"ga": 100, "test_contact": 200, "ops": 300}[name]
                ),
                f"traefik.http.routers.{slug}.rule": f"PathPrefix(`/v2/{slug}`)",
            }
        },
        "health": {
            "url": f"http://127.0.0.1:{port}/readyz",
            "status_code": 200,
        },
        "heartbeat": {
            "url": f"http://127.0.0.1:{port}/heartbeat",
            "status_code": 200,
            "queues": ["effects", "outbox"],
        },
        "files": [
            {
                "path": f"runtime/{name}.py",
                "sha256": _digest(file_bytes),
            }
        ],
        "routing": {"target_hash": f"sha256:{marker * 64}"},
    }


def minimal_manifest() -> dict[str, Any]:
    return {
        "schema": "agente-v2-active-runtime-v1",
        "generated_at": "2026-09-05T12:00:00Z",
        "repository": {"remote": "origin"},
        "components": {name: _component(name) for name in COMPONENT_NAMES},
        "legacy": {"state": "excluded"},
        "generic_pointers": [
            {
                "name": name,
                "url": f"https://runtime.invalid/pointers/{name}",
                "sha256": _digest(_pointer_bytes(name)),
            }
            for name in COMPONENT_NAMES
        ],
        "verification": {
            "documented_variables": [
                "SERVICE_SUBSCRIBER_ID",
                "SERVICE_API_KEY",
                "SERVICE_SECRET",
                "SERVICE_TOKEN",
                "SERVICE_EMAIL",
                "SERVICE_PHONE",
            ]
        },
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_load_manifest_accepts_closed_synthetic_fixture(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    assert load_manifest(path) == manifest


@pytest.mark.parametrize(
    "missing",
    (
        "schema",
        "generated_at",
        "repository",
        "components",
        "legacy",
        "generic_pointers",
        "verification",
    ),
)
def test_manifest_requires_every_top_level(tmp_path: Path, missing: str) -> None:
    manifest = minimal_manifest()
    del manifest[missing]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="top-level"):
        load_manifest(path)


def test_manifest_rejects_unknown_top_level(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["notes"] = "synthetic"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="top-level"):
        load_manifest(path)


def test_manifest_rejects_unknown_nested_field(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["ga"]["container"]["privileged"] = False
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="unknown field"):
        load_manifest(path)


def test_manifest_rejects_wrong_schema_version(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["schema"] = "agente-v2-active-runtime-v2"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="unsupported schema"):
        load_manifest(path)


def test_manifest_rejects_secret_bearing_keys(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["api_key"] = "never-allowed"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="forbidden key"):
        load_manifest(path)


@pytest.mark.parametrize(
    "unsafe",
    (
        "subscriber_id=synthetic",
        "api-key=synthetic",
        "contains secret material",
        "access_token=synthetic",
        "customer_email=synthetic",
        "customer-phone=synthetic",
        "person@example.invalid",
        "+55 00 00000-0000",
    ),
)
def test_manifest_rejects_sensitive_string_patterns(
    tmp_path: Path, unsafe: str
) -> None:
    manifest = minimal_manifest()
    manifest["repository"]["remote"] = unsafe
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="forbidden string"):
        load_manifest(path)


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "ACTIVE_RUNTIME.json"
    path.write_text(
        '{"schema":"agente-v2-active-runtime-v1","schema":"duplicate"}',
        encoding="utf-8",
    )

    with pytest.raises(AuthorityError, match="duplicate key"):
        load_manifest(path)


def test_manifest_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "ACTIVE_RUNTIME.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(AuthorityError, match="JSON object"):
        load_manifest(path)


def test_default_runner_applies_a_bounded_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        observed["args"] = args
        observed.update(kwargs)
        return SimpleNamespace(returncode=7, stdout="out", stderr="err")

    monkeypatch.setattr(authority.subprocess, "run", fake_run)

    completed = authority._default_runner(["safe-command", "argument"])

    assert completed == authority.Completed(7, "out", "err")
    assert observed == {
        "args": ["safe-command", "argument"],
        "capture_output": True,
        "check": False,
        "encoding": "utf-8",
        "errors": "strict",
        "text": True,
        "timeout": 10.0,
    }


def test_render_uses_fixed_component_order() -> None:
    manifest = minimal_manifest()
    manifest["components"] = {
        name: manifest["components"][name]
        for name in ("ops", "ga", "test_contact")
    }

    rendered = render_markdown(manifest)

    assert rendered.index("## GA") < rendered.index("## Test contact")
    assert rendered.index("## Test contact") < rendered.index("## Ops")


def test_render_never_emits_target_identity() -> None:
    manifest = minimal_manifest()
    manifest["components"]["test_contact"]["routing"]["target_hash"] = (
        "sha256:" + "a" * 64
    )

    rendered = render_markdown(manifest)

    assert "sha256:" in rendered
    assert "subscriber" not in rendered.lower()


def test_render_emits_only_generic_pointer_digests() -> None:
    manifest = minimal_manifest()

    rendered = render_markdown(manifest)

    for pointer in manifest["generic_pointers"]:
        assert pointer["sha256"] in rendered
        assert pointer["url"] not in rendered


class FakeRuntime:
    """Synthetic command/HTTP boundary for verifier contract tests."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.calls: list[tuple[str, ...]] = []
        self.http_calls: list[str] = []
        self.command_failures: dict[tuple[str, ...], authority.Completed] = {}
        self.command_exceptions: dict[tuple[str, ...], Exception] = {}
        self.http_exceptions: dict[str, Exception] = {}
        self.trees: dict[str, str] = {}
        self.local_refs: dict[str, str] = {}
        self.remote_refs: dict[str, str] = {}
        self.images: dict[str, dict[str, Any]] = {}
        self.containers: dict[str, dict[str, Any]] = {}
        self.git_files: dict[tuple[str, str], bytes] = {}
        self.container_files: dict[tuple[str, str], str] = {}
        self.http_responses: dict[str, tuple[int, bytes]] = {}

        for name in COMPONENT_NAMES:
            component = manifest["components"][name]
            source = component["source"]
            image = component["image"]
            container = component["container"]
            ref = f"refs/heads/production/{name}"
            self.trees[source["commit"]] = source["tree"]
            self.local_refs[ref] = source["commit"]
            self.remote_refs[ref] = source["commit"]
            self.images[image["ref"]] = {
                "Id": image["id"],
                "RepoDigests": [image["ref"]],
                "Config": {
                    "Labels": {
                        "org.opencontainers.image.revision": image["revision"]
                    }
                },
            }
            labels = {
                "com.docker.compose.project": container["compose_project"],
                "com.docker.compose.project.config_files": container["compose_config"],
                "com.docker.compose.service": container["compose_service"],
                **component["traefik"]["labels"],
            }
            self.containers[container["name"]] = {
                "Image": image["id"],
                "Config": {
                    "Image": image["ref"],
                    "User": container["user"],
                    "Env": ["ENV_SENTINEL_SHOULD_NOT_LEAK"],
                    "Labels": labels,
                },
                "State": {
                    "Status": container["status"],
                    "Health": {"Status": container["health"]},
                },
                "HostConfig": {
                    "ReadonlyRootfs": container["read_only"],
                    "CapDrop": container["cap_drop"],
                    "SecurityOpt": container["security_opt"],
                },
                "Mounts": [
                    {
                        "Source": mount["source"],
                        "Destination": mount["destination"],
                        "RW": not mount["read_only"],
                    }
                    for mount in container["mounts"]
                ],
            }
            for file_entry in component["files"]:
                path = file_entry["path"]
                self.git_files[(source["commit"], path)] = _file_bytes(name)
                self.container_files[(container["name"], path)] = file_entry["sha256"]

            self.http_responses[component["health"]["url"]] = (200, b"ready\n")
            self.http_responses[component["heartbeat"]["url"]] = (
                200,
                json.dumps(
                    {
                        "status": "healthy",
                        "queues": {
                            queue: {"status": "healthy"}
                            for queue in component["heartbeat"]["queues"]
                        },
                    }
                ).encode(),
            )

        for pointer in manifest["generic_pointers"]:
            self.http_responses[pointer["url"]] = (
                200,
                _pointer_bytes(pointer["name"]),
            )

    def runner(self, raw_args: Any) -> authority.Completed:
        assert type(raw_args) is list
        args = tuple(raw_args)
        self.calls.append(args)
        if args in self.command_exceptions:
            raise self.command_exceptions[args]
        if args in self.command_failures:
            return self.command_failures[args]

        if args[:2] == ("git", "rev-parse") and len(args) == 3:
            commit = args[2].removesuffix("^{tree}")
            return authority.Completed(0, self.trees[commit] + "\n", "")
        if args[:3] == ("git", "show-ref", "--verify") and len(args) == 4:
            ref = args[3]
            return authority.Completed(0, f"{self.local_refs[ref]} {ref}\n", "")
        if args[:3] == ("git", "ls-remote", "--heads") and len(args) == 5:
            ref = args[4]
            return authority.Completed(0, f"{self.remote_refs[ref]}\t{ref}\n", "")
        if args[:3] == ("docker", "image", "inspect") and len(args) == 4:
            return authority.Completed(0, json.dumps([self.images[args[3]]]), "")
        if args[:2] == ("docker", "inspect") and len(args) == 3:
            return authority.Completed(0, json.dumps([self.containers[args[2]]]), "")
        if args[:2] == ("git", "show") and len(args) == 3:
            commit, path = args[2].split(":", 1)
            return authority.Completed(
                0,
                self.git_files[(commit, path)].decode("utf-8"),
                "",
            )
        if (
            args[:2] == ("docker", "exec")
            and len(args) == 7
            and args[3:5] == ("python", "-c")
        ):
            return authority.Completed(
                0,
                self.container_files[(args[2], args[6])] + "\n",
                "",
            )
        raise AssertionError(f"unexpected verifier command: {args!r}")

    def http_get(self, url: str) -> tuple[int, bytes]:
        self.http_calls.append(url)
        if url in self.http_exceptions:
            raise self.http_exceptions[url]
        return self.http_responses[url]


def test_verify_manifest_checks_every_declared_authority_surface() -> None:
    manifest = minimal_manifest()
    fake = FakeRuntime(manifest)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert errors == []
    for name in COMPONENT_NAMES:
        component = manifest["components"][name]
        source = component["source"]
        image = component["image"]
        container = component["container"]
        ref = f"refs/heads/production/{name}"
        assert ("git", "rev-parse", f"{source['commit']}^{{tree}}") in fake.calls
        assert ("git", "show-ref", "--verify", ref) in fake.calls
        assert ("git", "ls-remote", "--heads", "origin", ref) in fake.calls
        assert ("docker", "image", "inspect", image["ref"]) in fake.calls
        assert ("docker", "inspect", container["name"]) in fake.calls
        assert component["health"]["url"] in fake.http_calls
        assert component["heartbeat"]["url"] in fake.http_calls
        for file_entry in component["files"]:
            assert (
                "git",
                "show",
                f"{source['commit']}:{file_entry['path']}",
            ) in fake.calls
            matching_execs = [
                call
                for call in fake.calls
                if call[:4] == ("docker", "exec", container["name"], "python")
                and call[-1] == file_entry["path"]
            ]
            assert len(matching_execs) == 1
    for pointer in manifest["generic_pointers"]:
        assert pointer["url"] in fake.http_calls


@pytest.mark.parametrize(
    ("surface", "expected_error"),
    (
        ("tree", "git tree"),
        ("local_ref", "local production ref"),
        ("remote_ref", "remote production ref"),
        ("image_id", "image id"),
        ("image_revision", "OCI revision"),
        ("container_image", "container image id"),
        ("container_ref", "container image ref"),
        ("compose_project", "compose project"),
        ("compose_config", "compose config"),
        ("compose_service", "compose service"),
        ("status", "container status"),
        ("health", "container health"),
        ("user", "container user"),
        ("read_only", "read-only rootfs"),
        ("cap_drop", "cap-drop"),
        ("security_opt", "security options"),
        ("mounts", "mounts"),
        ("traefik", "Traefik labels"),
        ("health_endpoint", "health endpoint"),
        ("heartbeat_json", "heartbeat JSON"),
        ("heartbeat_queue", "heartbeat queue"),
        ("git_file", "source file hash"),
        ("container_file", "container file hash"),
        ("pointer", "generic pointer"),
    ),
)
def test_verify_manifest_fails_closed_on_mismatch(
    surface: str, expected_error: str
) -> None:
    manifest = minimal_manifest()
    fake = FakeRuntime(manifest)
    component = manifest["components"]["ga"]
    source = component["source"]
    image = component["image"]
    container = component["container"]
    ref = "refs/heads/production/ga"
    container_doc = fake.containers[container["name"]]

    if surface == "tree":
        fake.trees[source["commit"]] = "f" * 40
    elif surface == "local_ref":
        fake.local_refs[ref] = "f" * 40
    elif surface == "remote_ref":
        fake.remote_refs[ref] = "f" * 40
    elif surface == "image_id":
        fake.images[image["ref"]]["Id"] = "sha256:" + "f" * 64
    elif surface == "image_revision":
        fake.images[image["ref"]]["Config"]["Labels"][
            "org.opencontainers.image.revision"
        ] = "f" * 40
    elif surface == "container_image":
        container_doc["Image"] = "sha256:" + "f" * 64
    elif surface == "container_ref":
        container_doc["Config"]["Image"] = "registry.invalid/wrong@sha256:" + "f" * 64
    elif surface == "compose_project":
        container_doc["Config"]["Labels"]["com.docker.compose.project"] = "wrong"
    elif surface == "compose_config":
        container_doc["Config"]["Labels"][
            "com.docker.compose.project.config_files"
        ] = "/wrong/compose.yml"
    elif surface == "compose_service":
        container_doc["Config"]["Labels"]["com.docker.compose.service"] = "wrong"
    elif surface == "status":
        container_doc["State"]["Status"] = "exited"
    elif surface == "health":
        container_doc["State"]["Health"]["Status"] = "unhealthy"
    elif surface == "user":
        container_doc["Config"]["User"] = "0"
    elif surface == "read_only":
        container_doc["HostConfig"]["ReadonlyRootfs"] = False
    elif surface == "cap_drop":
        container_doc["HostConfig"]["CapDrop"] = []
    elif surface == "security_opt":
        container_doc["HostConfig"]["SecurityOpt"] = []
    elif surface == "mounts":
        container_doc["Mounts"][0]["RW"] = True
    elif surface == "traefik":
        container_doc["Config"]["Labels"][
            "traefik.http.routers.ga.priority"
        ] = "1"
    elif surface == "health_endpoint":
        fake.http_responses[component["health"]["url"]] = (503, b"not ready")
    elif surface == "heartbeat_json":
        fake.http_responses[component["heartbeat"]["url"]] = (200, b"not-json")
    elif surface == "heartbeat_queue":
        heartbeat = json.loads(fake.http_responses[component["heartbeat"]["url"]][1])
        heartbeat["queues"]["effects"]["status"] = "stalled"
        fake.http_responses[component["heartbeat"]["url"]] = (
            200,
            json.dumps(heartbeat).encode(),
        )
    elif surface == "git_file":
        fake.git_files[(source["commit"], component["files"][0]["path"])] = b"wrong"
    elif surface == "container_file":
        fake.container_files[
            (container["name"], component["files"][0]["path"])
        ] = "sha256:" + "f" * 64
    elif surface == "pointer":
        pointer = manifest["generic_pointers"][0]
        fake.http_responses[pointer["url"]] = (200, b"wrong")
    else:  # pragma: no cover - guards the test matrix itself
        raise AssertionError(surface)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any(expected_error in error for error in errors), errors
    assert all("ENV_SENTINEL_SHOULD_NOT_LEAK" not in error for error in errors)


def test_verify_manifest_never_copies_docker_inspect_output_to_diagnostics() -> None:
    manifest = minimal_manifest()
    fake = FakeRuntime(manifest)
    container_name = manifest["components"]["ga"]["container"]["name"]
    command = ("docker", "inspect", container_name)
    fake.command_failures[command] = authority.Completed(
        1,
        '{"Config":{"Env":["ENV_SENTINEL_SHOULD_NOT_LEAK"]}}',
        "ENV_SENTINEL_SHOULD_NOT_LEAK",
    )

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any("container inspect failed" in error for error in errors)
    assert all("ENV_SENTINEL_SHOULD_NOT_LEAK" not in error for error in errors)


def test_verify_manifest_converts_boundary_exceptions_to_sanitized_errors() -> None:
    manifest = minimal_manifest()
    fake = FakeRuntime(manifest)
    commit = manifest["components"]["ga"]["source"]["commit"]
    health_url = manifest["components"]["ops"]["health"]["url"]
    fake.command_exceptions[("git", "rev-parse", f"{commit}^{{tree}}")] = TimeoutError(
        "ENV_SENTINEL_SHOULD_NOT_LEAK"
    )
    fake.http_exceptions[health_url] = OSError("ENV_SENTINEL_SHOULD_NOT_LEAK")

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any("git tree command failed" in error for error in errors)
    assert any("health endpoint request failed" in error for error in errors)
    assert all("ENV_SENTINEL_SHOULD_NOT_LEAK" not in error for error in errors)


def test_verify_manifest_validates_before_running_boundaries() -> None:
    manifest = minimal_manifest()
    manifest["components"]["ga"]["container"]["read_only"] = False
    fake = FakeRuntime(minimal_manifest())

    with pytest.raises(AuthorityError, match="read_only must be true"):
        authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert fake.calls == []
    assert fake.http_calls == []


def test_cli_render_and_verify_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = minimal_manifest()
    manifest_path = tmp_path / "ACTIVE_RUNTIME.json"
    markdown_path = tmp_path / "ACTIVE_RUNTIME.md"
    _write_manifest(manifest_path, manifest)

    assert authority.main(
        ["render", "--manifest", str(manifest_path), "--output", str(markdown_path)]
    ) == 0
    assert markdown_path.read_text(encoding="utf-8") == render_markdown(manifest)

    fake = FakeRuntime(manifest)
    monkeypatch.setattr(authority, "_default_runner", fake.runner)
    monkeypatch.setattr(authority, "_default_http_get", fake.http_get)

    assert authority.main(["verify", "--manifest", str(manifest_path), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"ok": True, "errors": []}


def test_script_entrypoint_renders_with_the_declared_cli(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest_path = tmp_path / "ACTIVE_RUNTIME.json"
    markdown_path = tmp_path / "ACTIVE_RUNTIME.md"
    _write_manifest(manifest_path, manifest)

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(authority.__file__)),
            "render",
            "--manifest",
            str(manifest_path),
            "--output",
            str(markdown_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert markdown_path.read_text(encoding="utf-8") == render_markdown(manifest)
