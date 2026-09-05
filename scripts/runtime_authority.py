#!/usr/bin/env python3
"""Render and verify the versioned Agente V2 runtime authority manifest.

The manifest is deliberately closed and contains only synthetic-safe operational
identifiers.  Verification returns sanitized diagnostics and never includes raw
Docker inspect payloads (notably ``Config.Env``).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any, Final
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


SCHEMA: Final = "agente-v2-active-runtime-v1"
COMPONENT_ORDER: Final = ("ga", "test_contact", "ops")
_COMMAND_TIMEOUT_SECONDS: Final = 10.0
_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema",
        "generated_at",
        "repository",
        "components",
        "legacy",
        "generic_pointers",
        "verification",
    }
)
_SENSITIVE_TERM_RE: Final = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:subscriber[_-]?id|api[_-]?key|secret|(?:access[_-]?)?token|email|phone)"
    r"(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_EMAIL_RE: Final = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?![A-Za-z0-9.-])"
)
_PHONE_RE: Final = re.compile(r"(?<!\d)\+(?:[\s().-]*\d){10,15}(?!\d)")
_ENVIRONMENT_NAME_RE: Final = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
_GIT_OBJECT_RE: Final = re.compile(r"[0-9a-f]{40}")
_SHA256_RE: Final = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_NAME_RE: Final = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_SAFE_NAME_RE: Final = re.compile(r"[a-z0-9][a-z0-9_.-]*")


class AuthorityError(RuntimeError):
    """The authority manifest or verification environment is not trustworthy."""


@dataclass(frozen=True, slots=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[Sequence[str]], Completed]
HttpGet = Callable[[str], tuple[int, bytes]]


def _default_runner(args: Sequence[str]) -> Completed:
    """Run a verifier command with a fixed timeout and captured output."""

    completed = subprocess.run(
        list(args),
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="strict",
        text=True,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )
    return Completed(completed.returncode, completed.stdout, completed.stderr)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityError(f"duplicate key in manifest: {key}")
        result[key] = value
    return result


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path) if path else "manifest"


def _scan_sensitive(
    value: object,
    *,
    path: tuple[str, ...] = (),
    documented_variable: bool = False,
) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if type(key) is not str:
                raise AuthorityError(f"non-text key at {_path_text(path)}")
            if _SENSITIVE_TERM_RE.search(key):
                raise AuthorityError(f"forbidden key at {_path_text(path + (key,))}")
            child_path = path + (key,)
            _scan_sensitive(nested, path=child_path)
        return
    if isinstance(value, list):
        is_documented_list = path == ("verification", "documented_variables")
        for index, nested in enumerate(value):
            _scan_sensitive(
                nested,
                path=path + (str(index),),
                documented_variable=is_documented_list,
            )
        return
    if type(value) is str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AuthorityError(f"control character at {_path_text(path)}")
        if documented_variable and _ENVIRONMENT_NAME_RE.fullmatch(value):
            return
        if (
            _SENSITIVE_TERM_RE.search(value)
            or _EMAIL_RE.search(value)
            or _PHONE_RE.search(value)
        ):
            raise AuthorityError(f"forbidden string at {_path_text(path)}")
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    raise AuthorityError(f"non-JSON value at {_path_text(path)}")


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AuthorityError(f"{path} must be a JSON object")
    if not all(type(key) is str for key in value):
        raise AuthorityError(f"{path} contains a non-text key")
    return value


def _list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise AuthorityError(f"{path} must be a JSON array")
    return value


def _exact_fields(
    value: object,
    expected: set[str] | frozenset[str],
    path: str,
    *,
    top_level: bool = False,
) -> Mapping[str, object]:
    mapping = _mapping(value, path)
    actual = set(mapping)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        kind = "top-level fields" if top_level else "fields"
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown field=" + ",".join(unknown))
        raise AuthorityError(f"{path} {kind} mismatch ({'; '.join(details)})")
    return mapping


def _text(value: object, path: str) -> str:
    if type(value) is not str or not value:
        raise AuthorityError(f"{path} must be nonempty text")
    return value


def _literal(value: object, expected: str, path: str) -> str:
    text = _text(value, path)
    if text != expected:
        raise AuthorityError(f"{path} must equal {expected!r}")
    return text


def _true(value: object, path: str) -> None:
    if value is not True:
        raise AuthorityError(f"{path} must be true")


def _status_code(value: object, path: str) -> int:
    if type(value) is not int or value != 200:
        raise AuthorityError(f"{path} must equal 200")
    return value


def _git_object(value: object, path: str) -> str:
    text = _text(value, path)
    if not _GIT_OBJECT_RE.fullmatch(text):
        raise AuthorityError(f"{path} must be a lowercase 40-hex Git object")
    return text


def _sha256(value: object, path: str) -> str:
    text = _text(value, path)
    if not _SHA256_RE.fullmatch(text):
        raise AuthorityError(f"{path} must be a prefixed lowercase SHA-256")
    return text


def _url(value: object, path: str, *, health: bool = False) -> str:
    text = _text(value, path)
    parsed = urlsplit(text)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AuthorityError(f"{path} must be an HTTP URL without credentials or query")
    if health and not parsed.path.endswith(("/readyz", "/healthz")):
        raise AuthorityError(f"{path} must target /readyz or /healthz")
    return text


def _relative_path(value: object, path: str) -> str:
    text = _text(value, path)
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise AuthorityError(f"{path} must be a safe relative path")
    return candidate.as_posix()


def _absolute_path(value: object, path: str) -> str:
    text = _text(value, path)
    candidate = PurePosixPath(text)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise AuthorityError(f"{path} must be a normalized absolute path")
    return candidate.as_posix()


def _string_list(
    value: object,
    path: str,
    *,
    nonempty: bool = False,
) -> list[str]:
    raw = _list(value, path)
    result = [_text(item, f"{path}[{index}]") for index, item in enumerate(raw)]
    if nonempty and not result:
        raise AuthorityError(f"{path} must not be empty")
    if len(result) != len(set(result)):
        raise AuthorityError(f"{path} must not contain duplicates")
    return result


def _validate_component(name: str, value: object) -> None:
    path = f"components.{name}"
    component = _exact_fields(
        value,
        {
            "source",
            "image",
            "container",
            "traefik",
            "health",
            "heartbeat",
            "files",
            "routing",
        },
        path,
    )

    source = _exact_fields(component["source"], {"commit", "tree"}, f"{path}.source")
    commit = _git_object(source["commit"], f"{path}.source.commit")
    _git_object(source["tree"], f"{path}.source.tree")

    image = _exact_fields(
        component["image"], {"ref", "id", "revision"}, f"{path}.image"
    )
    image_ref = _text(image["ref"], f"{path}.image.ref")
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image_ref):
        raise AuthorityError(f"{path}.image.ref must be digest-pinned")
    _sha256(image["id"], f"{path}.image.id")
    revision = _git_object(image["revision"], f"{path}.image.revision")
    if revision != commit:
        raise AuthorityError(f"{path}.image.revision must equal source commit")

    container = _exact_fields(
        component["container"],
        {
            "name",
            "compose_project",
            "compose_config",
            "compose_service",
            "status",
            "health",
            "user",
            "read_only",
            "cap_drop",
            "security_opt",
            "mounts",
        },
        f"{path}.container",
    )
    container_name = _text(container["name"], f"{path}.container.name")
    if not _CONTAINER_NAME_RE.fullmatch(container_name):
        raise AuthorityError(f"{path}.container.name is invalid")
    _text(container["compose_project"], f"{path}.container.compose_project")
    _absolute_path(container["compose_config"], f"{path}.container.compose_config")
    _text(container["compose_service"], f"{path}.container.compose_service")
    _literal(container["status"], "running", f"{path}.container.status")
    _literal(container["health"], "healthy", f"{path}.container.health")
    user = _text(container["user"], f"{path}.container.user")
    if user.casefold() in {"root", "0", "0:0"}:
        raise AuthorityError(f"{path}.container.user must be non-root")
    _true(container["read_only"], f"{path}.container.read_only")
    if _string_list(container["cap_drop"], f"{path}.container.cap_drop") != ["ALL"]:
        raise AuthorityError(f"{path}.container.cap_drop must be exactly ALL")
    if _string_list(
        container["security_opt"], f"{path}.container.security_opt"
    ) != ["no-new-privileges:true"]:
        raise AuthorityError(
            f"{path}.container.security_opt must enable no-new-privileges"
        )
    mounts = _list(container["mounts"], f"{path}.container.mounts")
    destinations: set[str] = set()
    for index, raw_mount in enumerate(mounts):
        mount_path = f"{path}.container.mounts[{index}]"
        mount = _exact_fields(
            raw_mount,
            {"source", "destination", "read_only"},
            mount_path,
        )
        _absolute_path(mount["source"], f"{mount_path}.source")
        destination = _absolute_path(
            mount["destination"], f"{mount_path}.destination"
        )
        if destination in destinations:
            raise AuthorityError(f"{path}.container.mounts has duplicate destinations")
        destinations.add(destination)
        if type(mount["read_only"]) is not bool:
            raise AuthorityError(f"{mount_path}.read_only must be boolean")

    traefik = _exact_fields(component["traefik"], {"labels"}, f"{path}.traefik")
    labels = _mapping(traefik["labels"], f"{path}.traefik.labels")
    if not labels:
        raise AuthorityError(f"{path}.traefik.labels must not be empty")
    priorities = 0
    for label, raw_expected in labels.items():
        if not label.startswith("traefik."):
            raise AuthorityError(f"{path}.traefik.labels contains a non-Traefik label")
        expected = _text(raw_expected, f"{path}.traefik.labels.{label}")
        if label.endswith(".priority"):
            priorities += 1
            if not expected.isdecimal() or int(expected) <= 0:
                raise AuthorityError(f"{path}.traefik priority must be positive")
    if priorities == 0:
        raise AuthorityError(f"{path}.traefik.labels must declare a router priority")

    health = _exact_fields(
        component["health"], {"url", "status_code"}, f"{path}.health"
    )
    _url(health["url"], f"{path}.health.url", health=True)
    _status_code(health["status_code"], f"{path}.health.status_code")

    heartbeat = _exact_fields(
        component["heartbeat"],
        {"url", "status_code", "queues"},
        f"{path}.heartbeat",
    )
    _url(heartbeat["url"], f"{path}.heartbeat.url")
    _status_code(heartbeat["status_code"], f"{path}.heartbeat.status_code")
    queues = _string_list(
        heartbeat["queues"], f"{path}.heartbeat.queues", nonempty=True
    )
    if any(not _SAFE_NAME_RE.fullmatch(queue) for queue in queues):
        raise AuthorityError(f"{path}.heartbeat.queues contains an invalid name")

    files = _list(component["files"], f"{path}.files")
    if not files:
        raise AuthorityError(f"{path}.files must not be empty")
    file_paths: set[str] = set()
    for index, raw_file in enumerate(files):
        file_path = f"{path}.files[{index}]"
        file_entry = _exact_fields(raw_file, {"path", "sha256"}, file_path)
        relative = _relative_path(file_entry["path"], f"{file_path}.path")
        if relative in file_paths:
            raise AuthorityError(f"{path}.files has duplicate paths")
        file_paths.add(relative)
        _sha256(file_entry["sha256"], f"{file_path}.sha256")

    routing = _exact_fields(
        component["routing"], {"target_hash"}, f"{path}.routing"
    )
    _sha256(routing["target_hash"], f"{path}.routing.target_hash")


def _validate_manifest(manifest: Mapping[str, object]) -> Mapping[str, object]:
    _scan_sensitive(manifest)
    root = _exact_fields(
        manifest,
        _TOP_LEVEL_FIELDS,
        "manifest",
        top_level=True,
    )
    schema = _text(root["schema"], "schema")
    if schema != SCHEMA:
        raise AuthorityError(f"unsupported schema: {schema}")

    generated_at = _text(root["generated_at"], "generated_at")
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorityError("generated_at must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
        raise AuthorityError("generated_at must include a UTC offset")

    repository = _exact_fields(root["repository"], {"remote"}, "repository")
    _literal(repository["remote"], "origin", "repository.remote")

    components = _exact_fields(
        root["components"], set(COMPONENT_ORDER), "components"
    )
    for name in COMPONENT_ORDER:
        _validate_component(name, components[name])

    legacy = _exact_fields(root["legacy"], {"state"}, "legacy")
    _literal(legacy["state"], "excluded", "legacy.state")

    pointers = _list(root["generic_pointers"], "generic_pointers")
    if not pointers:
        raise AuthorityError("generic_pointers must not be empty")
    pointer_names: set[str] = set()
    pointer_urls: set[str] = set()
    for index, raw_pointer in enumerate(pointers):
        path = f"generic_pointers[{index}]"
        pointer = _exact_fields(raw_pointer, {"name", "url", "sha256"}, path)
        name = _text(pointer["name"], f"{path}.name")
        if not _SAFE_NAME_RE.fullmatch(name) or name in pointer_names:
            raise AuthorityError(f"{path}.name must be unique and safe")
        pointer_names.add(name)
        url = _url(pointer["url"], f"{path}.url")
        if url in pointer_urls:
            raise AuthorityError(f"{path}.url must be unique")
        pointer_urls.add(url)
        _sha256(pointer["sha256"], f"{path}.sha256")

    verification = _exact_fields(
        root["verification"], {"documented_variables"}, "verification"
    )
    documented_variables = _string_list(
        verification["documented_variables"],
        "verification.documented_variables",
    )
    if any(
        not _ENVIRONMENT_NAME_RE.fullmatch(variable)
        for variable in documented_variables
    ):
        raise AuthorityError(
            "verification.documented_variables must contain variable names only"
        )
    return root


def load_manifest(path: Path) -> dict[str, object]:
    """Load and validate a closed V1 authority manifest from ``path``."""

    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AuthorityError("manifest could not be read as UTF-8") from exc
    try:
        decoded = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except AuthorityError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthorityError("manifest is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise AuthorityError("manifest must be a JSON object")
    validated = _validate_manifest(decoded)
    return dict(validated)


def _code(value: object) -> str:
    return "`" + html.escape(str(value), quote=True).replace("`", "&#96;") + "`"


def render_markdown(manifest: Mapping[str, object]) -> str:
    """Render a safe summary without exposing routing target identities."""

    validated = _validate_manifest(manifest)
    repository = _mapping(validated["repository"], "repository")
    components = _mapping(validated["components"], "components")
    lines = [
        "# Agente V2 active runtime authority",
        "",
        f"- Schema: {_code(validated['schema'])}",
        f"- Generated at: {_code(validated['generated_at'])}",
        f"- Repository remote: {_code(repository['remote'])}",
    ]
    display_names = {"ga": "GA", "test_contact": "Test contact", "ops": "Ops"}
    for name in COMPONENT_ORDER:
        component = _mapping(components[name], f"components.{name}")
        source = _mapping(component["source"], f"components.{name}.source")
        image = _mapping(component["image"], f"components.{name}.image")
        container = _mapping(component["container"], f"components.{name}.container")
        health = _mapping(component["health"], f"components.{name}.health")
        routing = _mapping(component["routing"], f"components.{name}.routing")
        lines.extend(
            [
                "",
                f"## {display_names[name]}",
                "",
                f"- Source commit: {_code(source['commit'])}",
                f"- Source tree: {_code(source['tree'])}",
                f"- Image: {_code(image['ref'])}",
                f"- Image ID: {_code(image['id'])}",
                f"- Container: {_code(container['name'])}",
                f"- Container state: {_code(container['status'])} / {_code(container['health'])}",
                f"- Health endpoint: {_code(health['url'])}",
                f"- Routing target SHA-256: {_code(routing['target_hash'])}",
            ]
        )

    legacy = _mapping(validated["legacy"], "legacy")
    lines.extend(["", "## Legacy", "", f"- State: {_code(legacy['state'])}"])
    lines.extend(["", "## Generic pointers", ""])
    for raw_pointer in _list(validated["generic_pointers"], "generic_pointers"):
        pointer = _mapping(raw_pointer, "generic_pointer")
        lines.append(f"- {_code(pointer['name'])}: {_code(pointer['sha256'])}")
    return "\n".join(lines) + "\n"


_CONTAINER_HASH_SCRIPT: Final = (
    "import hashlib,pathlib,sys;"
    "print('sha256:'+hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())"
)
_HEALTHY_STATES: Final = frozenset({"healthy", "ok", "ready"})


def _default_http_get(url: str) -> tuple[int, bytes]:
    """Issue a bounded GET and return only its status and body."""

    request = Request(url, method="GET")
    with urlopen(request, timeout=_COMMAND_TIMEOUT_SECONDS) as response:
        return response.status, response.read()


def _run_checked(
    runner: Runner,
    args: list[str],
    errors: list[str],
    failure: str,
) -> Completed | None:
    try:
        completed = runner(args)
    except Exception:
        errors.append(f"{failure} command failed")
        return None
    if not isinstance(completed, Completed) or completed.returncode != 0:
        errors.append(f"{failure} failed")
        return None
    if type(completed.stdout) is not str or type(completed.stderr) is not str:
        errors.append(f"{failure} returned an invalid result")
        return None
    return completed


def _http_checked(
    http_get: HttpGet,
    url: str,
    errors: list[str],
    failure: str,
) -> tuple[int, bytes] | None:
    try:
        result = http_get(url)
    except Exception:
        errors.append(f"{failure} request failed")
        return None
    if (
        type(result) is not tuple
        or len(result) != 2
        or type(result[0]) is not int
        or type(result[1]) is not bytes
    ):
        errors.append(f"{failure} returned an invalid response")
        return None
    return result


def _single_inspect_document(completed: Completed) -> Mapping[str, object] | None:
    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return None
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], Mapping)
        or not all(type(key) is str for key in payload[0])
    ):
        return None
    return payload[0]


def _nested_mapping(value: object, key: str) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get(key)
    if not isinstance(nested, Mapping) or not all(type(item) is str for item in nested):
        return None
    return nested


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _verify_git_authority(
    name: str,
    source: Mapping[str, object],
    runner: Runner,
    errors: list[str],
) -> None:
    commit = str(source["commit"])
    expected_tree = source["tree"]
    prefix = f"{name}:"
    tree = _run_checked(
        runner,
        ["git", "rev-parse", f"{commit}^{{tree}}"],
        errors,
        f"{prefix} git tree",
    )
    if tree is not None and tree.stdout.strip() != expected_tree:
        errors.append(f"{prefix} git tree mismatch")

    ref = f"refs/heads/production/{name}"
    local = _run_checked(
        runner,
        ["git", "show-ref", "--verify", ref],
        errors,
        f"{prefix} local production ref",
    )
    if local is not None and local.stdout.splitlines() != [f"{commit} {ref}"]:
        errors.append(f"{prefix} local production ref mismatch")

    remote = _run_checked(
        runner,
        ["git", "ls-remote", "--heads", "origin", ref],
        errors,
        f"{prefix} remote production ref",
    )
    if remote is not None and remote.stdout.splitlines() != [f"{commit}\t{ref}"]:
        errors.append(f"{prefix} remote production ref mismatch")


def _verify_image(
    name: str,
    image: Mapping[str, object],
    runner: Runner,
    errors: list[str],
) -> None:
    prefix = f"{name}:"
    completed = _run_checked(
        runner,
        ["docker", "image", "inspect", str(image["ref"])],
        errors,
        f"{prefix} image inspect",
    )
    if completed is None:
        return
    document = _single_inspect_document(completed)
    if document is None:
        errors.append(f"{prefix} image inspect returned invalid JSON")
        return
    if document.get("Id") != image["id"]:
        errors.append(f"{prefix} image id mismatch")
    repo_digests = document.get("RepoDigests")
    if not isinstance(repo_digests, list) or image["ref"] not in repo_digests:
        errors.append(f"{prefix} image digest ref mismatch")
    config = _nested_mapping(document, "Config")
    labels = _nested_mapping(config, "Labels")
    if labels is None or labels.get("org.opencontainers.image.revision") != image[
        "revision"
    ]:
        errors.append(f"{prefix} OCI revision mismatch")


def _actual_mounts(document: Mapping[str, object]) -> set[tuple[str, str, bool]] | None:
    mounts = document.get("Mounts")
    if not isinstance(mounts, list):
        return None
    result: set[tuple[str, str, bool]] = set()
    for mount in mounts:
        if not isinstance(mount, Mapping):
            return None
        source = mount.get("Source")
        destination = mount.get("Destination")
        writable = mount.get("RW")
        if type(source) is not str or type(destination) is not str or type(writable) is not bool:
            return None
        item = (source, destination, not writable)
        if item in result:
            return None
        result.add(item)
    return result


def _expected_mounts(container: Mapping[str, object]) -> set[tuple[str, str, bool]]:
    return {
        (str(mount["source"]), str(mount["destination"]), bool(mount["read_only"]))
        for mount in _list(container["mounts"], "container.mounts")
        if isinstance(mount, Mapping)
    }


def _verify_container(
    name: str,
    image: Mapping[str, object],
    container: Mapping[str, object],
    traefik: Mapping[str, object],
    runner: Runner,
    errors: list[str],
) -> None:
    prefix = f"{name}:"
    completed = _run_checked(
        runner,
        ["docker", "inspect", str(container["name"])],
        errors,
        f"{prefix} container inspect",
    )
    if completed is None:
        return
    document = _single_inspect_document(completed)
    if document is None:
        errors.append(f"{prefix} container inspect returned invalid JSON")
        return

    config = _nested_mapping(document, "Config")
    state = _nested_mapping(document, "State")
    host_config = _nested_mapping(document, "HostConfig")
    labels = _nested_mapping(config, "Labels")
    health = _nested_mapping(state, "Health")

    if document.get("Image") != image["id"]:
        errors.append(f"{prefix} container image id mismatch")
    if config is None or config.get("Image") != image["ref"]:
        errors.append(f"{prefix} container image ref mismatch")

    compose_fields = (
        ("com.docker.compose.project", "compose_project", "compose project"),
        (
            "com.docker.compose.project.config_files",
            "compose_config",
            "compose config",
        ),
        ("com.docker.compose.service", "compose_service", "compose service"),
    )
    for label, manifest_field, diagnostic in compose_fields:
        if labels is None or labels.get(label) != container[manifest_field]:
            errors.append(f"{prefix} {diagnostic} mismatch")

    if state is None or state.get("Status") != container["status"]:
        errors.append(f"{prefix} container status mismatch")
    if health is None or health.get("Status") != container["health"]:
        errors.append(f"{prefix} container health mismatch")
    if config is None or config.get("User") != container["user"]:
        errors.append(f"{prefix} container user mismatch")
    if host_config is None or host_config.get("ReadonlyRootfs") is not True:
        errors.append(f"{prefix} read-only rootfs mismatch")
    if host_config is None or host_config.get("CapDrop") != ["ALL"]:
        errors.append(f"{prefix} cap-drop mismatch")
    if host_config is None or host_config.get("SecurityOpt") != [
        "no-new-privileges:true"
    ]:
        errors.append(f"{prefix} security options mismatch")
    if _actual_mounts(document) != _expected_mounts(container):
        errors.append(f"{prefix} mounts mismatch")

    expected_traefik = _mapping(traefik["labels"], f"components.{name}.traefik.labels")
    actual_traefik = (
        {key: value for key, value in labels.items() if key.startswith("traefik.")}
        if labels is not None
        else None
    )
    if actual_traefik != dict(expected_traefik):
        errors.append(f"{prefix} Traefik labels mismatch")


def _queue_healthy(value: object) -> bool:
    if type(value) is str:
        return value.casefold() in _HEALTHY_STATES
    if isinstance(value, Mapping):
        status = value.get("status")
        return type(status) is str and status.casefold() in _HEALTHY_STATES
    return False


def _verify_http(
    name: str,
    component: Mapping[str, object],
    http_get: HttpGet,
    errors: list[str],
) -> None:
    prefix = f"{name}:"
    health = _mapping(component["health"], f"components.{name}.health")
    response = _http_checked(
        http_get,
        str(health["url"]),
        errors,
        f"{prefix} health endpoint",
    )
    if response is not None and response[0] != health["status_code"]:
        errors.append(f"{prefix} health endpoint status mismatch")

    heartbeat = _mapping(component["heartbeat"], f"components.{name}.heartbeat")
    response = _http_checked(
        http_get,
        str(heartbeat["url"]),
        errors,
        f"{prefix} heartbeat",
    )
    if response is None:
        return
    status, body = response
    if status != heartbeat["status_code"]:
        errors.append(f"{prefix} heartbeat status mismatch")
        return
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        errors.append(f"{prefix} heartbeat JSON invalid")
        return
    if not isinstance(document, Mapping):
        errors.append(f"{prefix} heartbeat JSON invalid")
        return
    overall = document.get("status")
    if type(overall) is not str or overall.casefold() not in _HEALTHY_STATES:
        errors.append(f"{prefix} heartbeat status unhealthy")
    queues = document.get("queues")
    expected_queues = set(_string_list(heartbeat["queues"], "heartbeat.queues"))
    if not isinstance(queues, Mapping) or set(queues) != expected_queues:
        errors.append(f"{prefix} heartbeat queues mismatch")
        return
    for queue in sorted(expected_queues):
        if not _queue_healthy(queues[queue]):
            errors.append(f"{prefix} heartbeat queue unhealthy")


def _verify_files(
    name: str,
    component: Mapping[str, object],
    runner: Runner,
    errors: list[str],
) -> None:
    prefix = f"{name}:"
    source = _mapping(component["source"], f"components.{name}.source")
    container = _mapping(component["container"], f"components.{name}.container")
    for raw_file in _list(component["files"], f"components.{name}.files"):
        file_entry = _mapping(raw_file, f"components.{name}.file")
        path = str(file_entry["path"])
        expected = file_entry["sha256"]
        source_file = _run_checked(
            runner,
            ["git", "show", f"{source['commit']}:{path}"],
            errors,
            f"{prefix} source file hash",
        )
        if source_file is not None:
            actual = _digest_bytes(source_file.stdout.encode("utf-8"))
            if actual != expected:
                errors.append(f"{prefix} source file hash mismatch")

        container_file = _run_checked(
            runner,
            [
                "docker",
                "exec",
                str(container["name"]),
                "python",
                "-c",
                _CONTAINER_HASH_SCRIPT,
                path,
            ],
            errors,
            f"{prefix} container file hash",
        )
        if container_file is not None:
            actual = container_file.stdout.strip()
            if not _SHA256_RE.fullmatch(actual) or actual != expected:
                errors.append(f"{prefix} container file hash mismatch")


def _verify_generic_pointers(
    pointers: list[object],
    http_get: HttpGet,
    errors: list[str],
) -> None:
    for raw_pointer in pointers:
        pointer = _mapping(raw_pointer, "generic_pointer")
        response = _http_checked(
            http_get,
            str(pointer["url"]),
            errors,
            "generic pointer",
        )
        if response is None:
            continue
        status, body = response
        if status != 200:
            errors.append("generic pointer status mismatch")
        elif _digest_bytes(body) != pointer["sha256"]:
            errors.append("generic pointer hash mismatch")


def verify_manifest(
    manifest: Mapping[str, object],
    runner: Runner,
    http_get: HttpGet,
) -> list[str]:
    """Verify every declared authority boundary, returning sanitized failures."""

    validated = _validate_manifest(manifest)
    components = _mapping(validated["components"], "components")
    errors: list[str] = []
    for name in COMPONENT_ORDER:
        component = _mapping(components[name], f"components.{name}")
        source = _mapping(component["source"], f"components.{name}.source")
        image = _mapping(component["image"], f"components.{name}.image")
        container = _mapping(component["container"], f"components.{name}.container")
        traefik = _mapping(component["traefik"], f"components.{name}.traefik")
        _verify_git_authority(name, source, runner, errors)
        _verify_image(name, image, runner, errors)
        _verify_container(name, image, container, traefik, runner, errors)
        _verify_http(name, component, http_get, errors)
        _verify_files(name, component, runner, errors)
    _verify_generic_pointers(
        _list(validated["generic_pointers"], "generic_pointers"),
        http_get,
        errors,
    )
    return errors


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="render the authority Markdown")
    render.add_argument("--manifest", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)

    verify = subparsers.add_parser("verify", help="verify the active runtime")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the render or verify command without exposing boundary payloads."""

    arguments = _argument_parser().parse_args(argv)
    try:
        manifest = load_manifest(arguments.manifest)
        if arguments.command == "render":
            arguments.output.write_text(render_markdown(manifest), encoding="utf-8")
            return 0

        errors = verify_manifest(manifest, _default_runner, _default_http_get)
        if arguments.as_json:
            print(json.dumps({"ok": not errors, "errors": errors}))
        elif errors:
            for error in errors:
                print(f"FAIL: {error}")
        else:
            print("runtime authority: OK")
        return 1 if errors else 0
    except AuthorityError as exc:
        print(f"authority error: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("authority error: output could not be written", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
