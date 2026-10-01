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
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
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
        "public_endpoints",
        "generic_pointers",
        "verification",
    }
)
_COMPONENT_ROLES: Final = {
    "ga": ("api", "worker", "router"),
    "test_contact": ("api", "worker", "router"),
    "ops": ("web",),
}
_QUEUE_ORDER: Final = (
    "boundary_relay",
    "handoff",
    "inbox",
    "outcome_projector",
    "payment_initiation",
    "post_payment",
    "public_delivery",
    "reconciliation",
    "reservation",
    "settlement",
)
_HEARTBEAT_SCHEMA: Final = "v2-worker-heartbeat-v2"
_WORKSPACE_ROOT: Final = PurePosixPath("/home/ubuntu/workspace")
_DIRECTORY_OPEN_FLAGS: Final = (
    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
)
_REGULAR_FILE_OPEN_FLAGS: Final = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
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
_FILE_MODE_RE: Final = re.compile(r"0[0-7]{3}")
_CLEAN_COMPOSE_PREFIX: Final = (
    "env",
    "-i",
    "HOME=/home/ubuntu",
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "docker",
    "compose",
)


class AuthorityError(RuntimeError):
    """The authority manifest or verification environment is not trustworthy."""


@dataclass(frozen=True, slots=True)
class Completed:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class _AnchoredFileWitness:
    directories: tuple[tuple[int, int], ...]
    file: tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _AnchoredSymlinkWitness:
    directories: tuple[tuple[int, int], ...]
    link: tuple[int, int, int, int, int, int]
    target: str


Runner = Callable[[Sequence[str]], Completed]
HttpGet = Callable[[str], tuple[int, bytes]]


def _default_runner(args: Sequence[str]) -> Completed:
    """Run a verifier command with a fixed timeout and captured output."""

    completed = subprocess.run(
        list(args),
        capture_output=True,
        check=False,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )
    return Completed(completed.returncode, completed.stdout, completed.stderr)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthorityError("duplicate key in manifest")
        result[key] = value
    return result


def _path_text(path: tuple[str, ...]) -> str:
    return ".".join(path) if path else "manifest"


def _contains_sensitive_pattern(value: str) -> bool:
    return bool(
        _SENSITIVE_TERM_RE.search(value)
        or _EMAIL_RE.search(value)
        or _PHONE_RE.search(value)
    )


def _scan_sensitive_keys(value: object, *, path: tuple[str, ...] = ()) -> None:
    """Reject unsafe mapping keys without copying those keys into diagnostics."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if type(key) is not str:
                raise AuthorityError(f"non-text key at {_path_text(path)}")
            if any(ord(character) < 32 or ord(character) == 127 for character in key):
                raise AuthorityError(f"forbidden key at {_path_text(path)}")
            if _contains_sensitive_pattern(key):
                raise AuthorityError(f"forbidden key at {_path_text(path)}")
            _scan_sensitive_keys(nested, path=path + (key,))
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_sensitive_keys(nested, path=path + (str(index),))


def _is_declared_variable_location(value: str, path: tuple[str, ...]) -> bool:
    if not _ENVIRONMENT_NAME_RE.fullmatch(value):
        return False
    if path[:-1] == ("verification", "documented_variables"):
        return True
    return (
        len(path) == 4
        and path[0] == "components"
        and path[2:] == ("routing", "variable")
    )


def _scan_sensitive(value: object, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if type(key) is not str:
                raise AuthorityError(f"non-text key at {_path_text(path)}")
            if _contains_sensitive_pattern(key):
                raise AuthorityError(f"forbidden key at {_path_text(path)}")
            _scan_sensitive(nested, path=path + (key,))
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _scan_sensitive(nested, path=path + (str(index),))
        return
    if type(value) is str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise AuthorityError(f"control character at {_path_text(path)}")
        if _is_declared_variable_location(value, path):
            return
        if _contains_sensitive_pattern(value):
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
    if type(value) is not int or not 100 <= value <= 599:
        raise AuthorityError(f"{path} must be an HTTP status_code integer")
    return value


def _positive_integer(value: object, path: str) -> int:
    if type(value) is not int or value <= 0:
        raise AuthorityError(f"{path} must be a positive integer")
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
    if (
        candidate.is_absolute()
        or not candidate.parts
        or ".." in candidate.parts
        or candidate.as_posix() != text
        or ":" in text
        or "\\" in text
    ):
        raise AuthorityError(f"{path} must be a safe relative path")
    return candidate.as_posix()


def _absolute_path(value: object, path: str) -> str:
    text = _text(value, path)
    candidate = PurePosixPath(text)
    if (
        not candidate.is_absolute()
        or text.startswith("//")
        or ".." in candidate.parts
        or candidate.as_posix() != text
    ):
        raise AuthorityError(f"{path} must be a normalized absolute path")
    return candidate.as_posix()


def _path_is_within(candidate: PurePosixPath, root: PurePosixPath) -> bool:
    return candidate == root or root in candidate.parents


def _pointer_path(
    value: object,
    path: str,
    repository_path: PurePosixPath,
) -> str:
    normalized = _absolute_path(value, path)
    candidate = PurePosixPath(normalized)
    if not any(
        _path_is_within(candidate, root)
        for root in (repository_path, _WORKSPACE_ROOT)
    ):
        raise AuthorityError(f"{path} must be inside the allowed roots")
    return normalized


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


def _validate_mounts(container: Mapping[str, object], path: str) -> None:
    mounts = _list(container["mounts"], f"{path}.mounts")
    destinations: set[str] = set()
    for index, raw_mount in enumerate(mounts):
        mount_path = f"{path}.mounts[{index}]"
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
            raise AuthorityError(f"{path}.mounts has duplicate destinations")
        destinations.add(destination)
        if type(mount["read_only"]) is not bool:
            raise AuthorityError(f"{mount_path}.read_only must be boolean")


def _validate_traefik_labels(value: object, path: str) -> Mapping[str, object]:
    labels = _mapping(value, path)
    for label, raw_expected in labels.items():
        if not label.startswith("traefik."):
            raise AuthorityError(f"{path} contains a non-Traefik label")
        expected = _text(raw_expected, f"{path}.{label}")
        if not label.endswith(".priority"):
            continue
        namespace = label.removesuffix(".priority")
        if (
            not namespace.startswith("traefik.http.routers.")
            or namespace == "traefik.http.routers."
            or f"{namespace}.rule" not in labels
            or f"{namespace}.service" not in labels
            or re.fullmatch(r"[0-9]+", expected) is None
            or int(expected) <= 0
        ):
            raise AuthorityError(
                f"{path} priority requires a matching router rule and service"
            )
    return labels


def _validate_container(value: object, path: str) -> tuple[str, str, bool]:
    container = _exact_fields(
        value,
        {
            "role",
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
            "traefik_labels",
        },
        path,
    )
    role = _text(container["role"], f"{path}.role")
    if not _SAFE_NAME_RE.fullmatch(role):
        raise AuthorityError(f"{path}.role is invalid")
    name = _text(container["name"], f"{path}.name")
    if not _CONTAINER_NAME_RE.fullmatch(name):
        raise AuthorityError(f"{path}.name is invalid")
    _text(container["compose_project"], f"{path}.compose_project")
    _absolute_path(container["compose_config"], f"{path}.compose_config")
    _text(container["compose_service"], f"{path}.compose_service")
    _literal(container["status"], "running", f"{path}.status")

    health = container["health"]
    if health is not None:
        _literal(health, "healthy", f"{path}.health")

    user = container["user"]
    if type(user) is not str:
        raise AuthorityError(f"{path}.user must identify a non-root user")
    compact_user = re.sub(r"\s+", "", user).casefold()
    owner = compact_user.split(":", 1)[0]
    numeric_root = re.fullmatch(r"[0-9]+", owner) is not None and int(owner) == 0
    if not compact_user or not owner or owner == "root" or numeric_root:
        raise AuthorityError(f"{path}.user must identify a non-root user")

    _true(container["read_only"], f"{path}.read_only")
    if _string_list(container["cap_drop"], f"{path}.cap_drop") != ["ALL"]:
        raise AuthorityError(f"{path}.cap_drop must be exactly ALL")
    if _string_list(container["security_opt"], f"{path}.security_opt") != [
        "no-new-privileges:true"
    ]:
        raise AuthorityError(f"{path}.security_opt must enable no-new-privileges")
    _validate_mounts(container, path)
    labels = _validate_traefik_labels(
        container["traefik_labels"], f"{path}.traefik_labels"
    )
    return role, name, bool(labels)


def _validate_source_and_image(component: Mapping[str, object], path: str) -> None:
    source = _exact_fields(
        component["source"], {"commit", "tree", "ref"}, f"{path}.source"
    )
    commit = _git_object(source["commit"], f"{path}.source.commit")
    _git_object(source["tree"], f"{path}.source.tree")
    source_ref = _text(source["ref"], f"{path}.source.ref")
    ref_suffix = source_ref.removeprefix("refs/heads/production/")
    if (
        ref_suffix == source_ref
        or not ref_suffix
        or source_ref.endswith(("/", ".", ".lock"))
        or "//" in source_ref
        or ".." in source_ref
        or "@{" in source_ref
        or re.search(r"[~^:?*\[\]\\\s]", source_ref)
    ):
        raise AuthorityError(f"{path}.source.ref must be a full local production ref")

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


def _validate_heartbeat(name: str, value: object, path: str) -> None:
    if name == "ops":
        if value is not None:
            raise AuthorityError("ops heartbeat must be null")
        return
    if value is None:
        raise AuthorityError(f"{name} heartbeat must be an object")
    heartbeat = _exact_fields(
        value,
        {"path", "schema", "max_age_seconds", "queues"},
        path,
    )
    _absolute_path(heartbeat["path"], f"{path}.path")
    _literal(heartbeat["schema"], _HEARTBEAT_SCHEMA, f"{path}.schema")
    _positive_integer(heartbeat["max_age_seconds"], f"{path}.max_age_seconds")
    queues = _string_list(heartbeat["queues"], f"{path}.queues")
    if queues != list(_QUEUE_ORDER):
        raise AuthorityError(f"{path}.queues must equal the closed ordered queue set")


def _validate_routing(
    name: str,
    value: object,
    path: str,
    roles: set[str],
    documented_variables: set[str],
) -> None:
    if name != "test_contact":
        if value is not None:
            raise AuthorityError(f"{name} routing must be null")
        return
    if value is None:
        raise AuthorityError("test_contact routing must be an object")
    routing = _exact_fields(
        value,
        {"container_role", "variable", "target_hash"},
        path,
    )
    role = _text(routing["container_role"], f"{path}.container_role")
    if role != "router" or role not in roles:
        raise AuthorityError(f"{path}.container_role must identify the router")
    variable = _text(routing["variable"], f"{path}.variable")
    if (
        not _ENVIRONMENT_NAME_RE.fullmatch(variable)
        or variable not in documented_variables
    ):
        raise AuthorityError(f"{path} routing variable must be documented")
    _sha256(routing["target_hash"], f"{path}.target_hash")


def _validate_artifact_record(
    value: object,
    path: str,
    repository_path: PurePosixPath,
    *,
    env_file: bool = False,
) -> Mapping[str, object]:
    record = _exact_fields(value, {"path", "sha256", "mode"}, path)
    _pointer_path(record["path"], f"{path}.path", repository_path)
    _sha256(record["sha256"], f"{path}.sha256")
    mode = record["mode"]
    if type(mode) is not str or _FILE_MODE_RE.fullmatch(mode) is None:
        raise AuthorityError(f"{path}.mode must use 0NNN octal text")
    if env_file and mode != "0600":
        raise AuthorityError(f"{path}.mode must equal '0600'")
    return record


def _validate_rollback_command(
    name: str,
    value: object,
    path: str,
    *,
    compose_project: str,
    compose_services: tuple[str, ...],
    compose_path: str,
    env_path: str,
) -> list[str]:
    if not isinstance(value, list) or not value or not all(
        type(argument) is str and argument for argument in value
    ):
        raise AuthorityError(f"{path} must be a JSON argv array")
    command = list(value)
    if command[:2] != ["env", "-i"]:
        raise AuthorityError(f"{path} must start with env -i")
    if command[2:4] != list(_CLEAN_COMPOSE_PREFIX[2:4]):
        raise AuthorityError(f"{path} must use the exact clean environment prefix")
    if command[4:6] != ["docker", "compose"]:
        raise AuthorityError(f"{path} must invoke docker compose directly")
    if command.count("--project-name") != 1:
        raise AuthorityError(f"{path} --project-name must occur exactly once")
    project_index = command.index("--project-name")
    if project_index + 1 >= len(command) or command[project_index + 1] != compose_project:
        raise AuthorityError(f"{path} project must equal the component compose project")
    if command.count("--env-file") != 1:
        raise AuthorityError(f"{path} --env-file must occur exactly once")
    env_index = command.index("--env-file")
    if env_index + 1 >= len(command) or command[env_index + 1] != env_path:
        raise AuthorityError(f"{path} --env-file must equal rollback env_file.path")
    if command.count("-f") != 1:
        raise AuthorityError(f"{path} -f must occur exactly once")
    compose_index = command.index("-f")
    if compose_index + 1 >= len(command) or command[compose_index + 1] != compose_path:
        raise AuthorityError(f"{path} -f must equal rollback compose_manifest.path")
    if name == "ops":
        if (
            len(compose_services) != 1
            or _SAFE_NAME_RE.fullmatch(compose_services[0]) is None
        ):
            raise AuthorityError(f"{path} rollback service is invalid")
        tail = [
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            compose_services[0],
        ]
    else:
        tail = ["up", "-d", "--force-recreate"]
    expected = [
        *_CLEAN_COMPOSE_PREFIX,
        "--project-name",
        compose_project,
        "--env-file",
        env_path,
        "-f",
        compose_path,
        *tail,
    ]
    if command != expected:
        raise AuthorityError(f"{path} must use the closed rollback argv shape")
    return command


def _validate_deployment(
    name: str,
    component: Mapping[str, object],
    path: str,
    repository_path: PurePosixPath,
    containers: list[Mapping[str, object]],
) -> None:
    deployment = _exact_fields(
        component["deployment"],
        {"compose_manifest", "env_file", "rollback"},
        f"{path}.deployment",
    )
    active_compose = _validate_artifact_record(
        deployment["compose_manifest"],
        f"{path}.deployment.compose_manifest",
        repository_path,
    )
    _validate_artifact_record(
        deployment["env_file"],
        f"{path}.deployment.env_file",
        repository_path,
        env_file=True,
    )
    projects = {str(container["compose_project"]) for container in containers}
    if len(projects) != 1:
        raise AuthorityError(
            f"{path} containers must use one compose project; "
            "compose project is shared across components or internally inconsistent"
        )
    if any(
        container["compose_config"] != active_compose["path"]
        for container in containers
    ):
        raise AuthorityError(
            f"{path} container compose_config must equal the active compose_manifest"
        )
    rollback = _exact_fields(
        deployment["rollback"],
        {"status", "descriptor", "compose_manifest", "env_file", "command"},
        f"{path}.deployment.rollback",
    )
    _literal(
        rollback["status"],
        "prepared",
        f"{path}.deployment.rollback.status",
    )
    _validate_artifact_record(
        rollback["descriptor"],
        f"{path}.deployment.rollback.descriptor",
        repository_path,
    )
    rollback_compose = _validate_artifact_record(
        rollback["compose_manifest"],
        f"{path}.deployment.rollback.compose_manifest",
        repository_path,
    )
    rollback_env = _validate_artifact_record(
        rollback["env_file"],
        f"{path}.deployment.rollback.env_file",
        repository_path,
        env_file=True,
    )
    _validate_rollback_command(
        name,
        rollback["command"],
        f"{path}.deployment.rollback.command",
        compose_project=next(iter(projects)),
        compose_services=tuple(
            str(container["compose_service"]) for container in containers
        ),
        compose_path=str(rollback_compose["path"]),
        env_path=str(rollback_env["path"]),
    )


def _validate_component(
    name: str,
    value: object,
    documented_variables: set[str],
    repository_path: PurePosixPath,
) -> None:
    path = f"components.{name}"
    component = _exact_fields(
        value,
        {
            "source",
            "image",
            "containers",
            "deployment",
            "files",
            "heartbeat",
            "routing",
        },
        path,
    )
    _validate_source_and_image(component, path)

    raw_containers = _list(component["containers"], f"{path}.containers")
    if not raw_containers:
        raise AuthorityError(f"{path}.containers must not be empty")
    containers_by_role: dict[str, str] = {}
    container_names: set[str] = set()
    validated_containers: list[Mapping[str, object]] = []
    has_traefik = False
    for index, raw_container in enumerate(raw_containers):
        container = _mapping(raw_container, f"{path}.containers[{index}]")
        role, container_name, has_traefik_labels = _validate_container(
            container, f"{path}.containers[{index}]"
        )
        validated_containers.append(container)
        if role in containers_by_role:
            raise AuthorityError(f"{path} container roles must be unique")
        if container_name in container_names:
            raise AuthorityError(f"{path} container names must be unique")
        containers_by_role[role] = container_name
        container_names.add(container_name)
        has_traefik = has_traefik or has_traefik_labels

    expected_roles = _COMPONENT_ROLES[name]
    if set(containers_by_role) != set(expected_roles):
        raise AuthorityError(
            f"{path} container roles must equal {', '.join(expected_roles)}"
        )
    if not has_traefik:
        raise AuthorityError(f"{path} must have a container with Traefik labels")
    roles = set(containers_by_role)
    _validate_deployment(
        name,
        component,
        path,
        repository_path,
        validated_containers,
    )

    files = _list(component["files"], f"{path}.files")
    if not files:
        raise AuthorityError(f"{path}.files must not be empty")
    file_paths: set[str] = set()
    container_paths: set[str] = set()
    for index, raw_file in enumerate(files):
        file_path = f"{path}.files[{index}]"
        file_entry = _exact_fields(
            raw_file,
            {"path", "container_path", "container_role", "sha256"},
            file_path,
        )
        relative = _relative_path(file_entry["path"], f"{file_path}.path")
        if relative in file_paths:
            raise AuthorityError(f"{path}.files has duplicate paths")
        file_paths.add(relative)
        container_path = _absolute_path(
            file_entry["container_path"], f"{file_path}.container_path"
        )
        if container_path in container_paths:
            raise AuthorityError(f"{path}.files has duplicate container paths")
        container_paths.add(container_path)
        container_role = _text(
            file_entry["container_role"], f"{file_path}.container_role"
        )
        if container_role not in roles:
            raise AuthorityError(
                f"{file_path}.container_role must resolve to a declared container"
            )
        _sha256(file_entry["sha256"], f"{file_path}.sha256")

    _validate_heartbeat(name, component["heartbeat"], f"{path}.heartbeat")
    _validate_routing(
        name,
        component["routing"],
        f"{path}.routing",
        roles,
        documented_variables,
    )


def _validate_global_component_ownership(
    components: Mapping[str, object],
) -> None:
    container_owners: dict[str, str] = {}
    compose_project_owners: dict[str, str] = {}
    writable_mount_owners: dict[str, str] = {}
    for component_name in COMPONENT_ORDER:
        component = _mapping(
            components[component_name], f"components.{component_name}"
        )
        containers = _list(
            component["containers"], f"components.{component_name}.containers"
        )
        for raw_container in containers:
            container = _mapping(
                raw_container, f"components.{component_name}.container"
            )
            container_name = str(container["name"])
            owner = container_owners.setdefault(container_name, component_name)
            if owner != component_name:
                raise AuthorityError("container name is shared across components")

            compose_project = str(container["compose_project"])
            owner = compose_project_owners.setdefault(compose_project, component_name)
            if owner != component_name:
                raise AuthorityError("compose project is shared across components")

            for raw_mount in _list(container["mounts"], "container.mounts"):
                mount = _mapping(raw_mount, "container.mount")
                if mount["read_only"] is not False:
                    continue
                source = str(mount["source"])
                owner = writable_mount_owners.setdefault(source, component_name)
                if owner != component_name:
                    raise AuthorityError(
                        "writable mount source is shared across components"
                    )


def _deployment_records(
    component: Mapping[str, object],
) -> tuple[tuple[str, Mapping[str, object]], ...]:
    deployment = _mapping(component["deployment"], "component.deployment")
    rollback = _mapping(deployment["rollback"], "component.deployment.rollback")
    return (
        (
            "active-compose",
            _mapping(deployment["compose_manifest"], "deployment.compose_manifest"),
        ),
        ("active-env", _mapping(deployment["env_file"], "deployment.env_file")),
        (
            "rollback-descriptor",
            _mapping(rollback["descriptor"], "deployment.rollback.descriptor"),
        ),
        (
            "rollback-compose",
            _mapping(
                rollback["compose_manifest"],
                "deployment.rollback.compose_manifest",
            ),
        ),
        (
            "rollback-env",
            _mapping(rollback["env_file"], "deployment.rollback.env_file"),
        ),
    )


def _validate_deployment_pointer_coverage(
    components: Mapping[str, object],
    pointers: list[Mapping[str, object]],
) -> None:
    represented: set[tuple[str, str]] = set()
    for pointer in pointers:
        for candidate in (pointer["path"], pointer["symlink_target"]):
            if candidate is not None:
                represented.add((str(candidate), str(pointer["sha256"])))

    for name in COMPONENT_ORDER:
        component = _mapping(components[name], f"components.{name}")
        records = dict(_deployment_records(component))
        for artifact_name, record in records.items():
            artifact_path = str(record["path"])
            if artifact_name not in {"active-compose", "active-env"}:
                continue
            witness = (artifact_path, str(record["sha256"]))
            if witness not in represented:
                short_name = artifact_name.removeprefix("active-")
                raise AuthorityError(
                    f"components.{name} active {short_name} pointer is missing"
                )


def _validate_manifest(manifest: Mapping[str, object]) -> Mapping[str, object]:
    _scan_sensitive_keys(manifest)
    root = _exact_fields(
        manifest,
        _TOP_LEVEL_FIELDS,
        "manifest",
        top_level=True,
    )
    _scan_sensitive(root)

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

    repository = _exact_fields(root["repository"], {"path", "remote"}, "repository")
    repository_path = PurePosixPath(
        _absolute_path(repository["path"], "repository.path")
    )
    _literal(repository["remote"], "origin", "repository.remote")

    verification = _exact_fields(
        root["verification"], {"documented_variables"}, "verification"
    )
    documented_variable_list = _string_list(
        verification["documented_variables"],
        "verification.documented_variables",
    )
    if any(
        not _ENVIRONMENT_NAME_RE.fullmatch(variable)
        for variable in documented_variable_list
    ):
        raise AuthorityError(
            "verification.documented_variables must contain variable names only"
        )
    documented_variables = set(documented_variable_list)

    components = _exact_fields(
        root["components"], set(COMPONENT_ORDER), "components"
    )
    for component_name in COMPONENT_ORDER:
        _validate_component(
            component_name,
            components[component_name],
            documented_variables,
            repository_path,
        )
    _validate_global_component_ownership(components)

    legacy = _exact_fields(root["legacy"], {"path", "state"}, "legacy")
    _absolute_path(legacy["path"], "legacy.path")
    _literal(legacy["state"], "excluded", "legacy.state")

    endpoints = _list(root["public_endpoints"], "public_endpoints")
    if not endpoints:
        raise AuthorityError("public_endpoints must not be empty")
    endpoint_names: set[str] = set()
    endpoint_urls: set[str] = set()
    for index, raw_endpoint in enumerate(endpoints):
        path = f"public_endpoints[{index}]"
        endpoint = _exact_fields(
            raw_endpoint,
            {"name", "url", "status_code", "body_sha256"},
            path,
        )
        endpoint_name = _text(endpoint["name"], f"{path}.name")
        if (
            not _SAFE_NAME_RE.fullmatch(endpoint_name)
            or endpoint_name in endpoint_names
        ):
            raise AuthorityError(f"{path}.name must be unique and safe")
        endpoint_names.add(endpoint_name)
        endpoint_url = _url(endpoint["url"], f"{path}.url")
        if endpoint_url in endpoint_urls:
            raise AuthorityError(f"{path}.url must be unique")
        endpoint_urls.add(endpoint_url)
        _status_code(endpoint["status_code"], f"{path}.status_code")
        _sha256(endpoint["body_sha256"], f"{path}.body_sha256")

    pointers = _list(root["generic_pointers"], "generic_pointers")
    if not pointers:
        raise AuthorityError("generic_pointers must not be empty")
    pointer_names: set[str] = set()
    pointer_paths: set[str] = set()
    validated_pointers: list[Mapping[str, object]] = []
    for index, raw_pointer in enumerate(pointers):
        path = f"generic_pointers[{index}]"
        pointer = _exact_fields(
            raw_pointer,
            {"name", "path", "sha256", "symlink_target"},
            path,
        )
        validated_pointers.append(pointer)
        pointer_name = _text(pointer["name"], f"{path}.name")
        if (
            not _SAFE_NAME_RE.fullmatch(pointer_name)
            or pointer_name in pointer_names
        ):
            raise AuthorityError(f"{path}.name must be unique and safe")
        pointer_names.add(pointer_name)
        pointer_path = _pointer_path(
            pointer["path"], f"{path}.path", repository_path
        )
        if pointer_path in pointer_paths:
            raise AuthorityError(f"{path}.path must be unique")
        pointer_paths.add(pointer_path)
        symlink_target = pointer["symlink_target"]
        if symlink_target is not None:
            _pointer_path(
                symlink_target,
                f"{path}.symlink_target",
                repository_path,
            )
        _sha256(pointer["sha256"], f"{path}.sha256")
    _validate_deployment_pointer_coverage(components, validated_pointers)
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
    """Render a safe summary without exposing any routing details."""

    validated = _validate_manifest(manifest)
    repository = _mapping(validated["repository"], "repository")
    components = _mapping(validated["components"], "components")
    lines = [
        "# Agente V2 active runtime authority",
        "",
        f"- Schema: {_code(validated['schema'])}",
        f"- Generated at: {_code(validated['generated_at'])}",
        f"- Repository path: {_code(repository['path'])}",
        f"- Repository remote: {_code(repository['remote'])}",
    ]
    display_names = {"ga": "GA", "test_contact": "Test contact", "ops": "Ops"}
    for name in COMPONENT_ORDER:
        component_path = f"components.{name}"
        component = _mapping(components[name], component_path)
        source = _mapping(component["source"], f"{component_path}.source")
        image = _mapping(component["image"], f"{component_path}.image")
        lines.extend(
            [
                "",
                f"## {display_names[name]}",
                "",
                f"- Source commit: {_code(source['commit'])}",
                f"- Source tree: {_code(source['tree'])}",
                f"- Source ref: {_code(source['ref'])}",
                f"- Image: {_code(image['ref'])}",
                f"- Image ID: {_code(image['id'])}",
                f"- Image revision: {_code(image['revision'])}",
                "",
                "### Containers",
            ]
        )

        containers_by_role: dict[str, Mapping[str, object]] = {}
        for index, raw_container in enumerate(
            _list(component["containers"], f"{component_path}.containers")
        ):
            container = _mapping(
                raw_container, f"{component_path}.containers[{index}]"
            )
            containers_by_role[str(container["role"])] = container
        for role in _COMPONENT_ROLES[name]:
            container = containers_by_role[role]
            health = container["health"] if container["health"] is not None else "null"
            lines.append(
                f"- {_code(role)}: {_code(container['name'])} "
                f"({_code(container['status'])}, health={_code(health)})"
            )

        mount_access: dict[str, set[str]] = {}
        mount_destinations: dict[str, list[tuple[str, str]]] = {}
        for role in _COMPONENT_ROLES[name]:
            container = containers_by_role[role]
            for raw_mount in _list(
                container["mounts"],
                f"{component_path}.containers.{role}.mounts",
            ):
                mount = _mapping(raw_mount, f"{component_path}.mount")
                source = str(mount["source"])
                mount_access.setdefault(source, set()).add(
                    "ro" if mount["read_only"] else "rw"
                )
                mount_destinations.setdefault(source, []).append(
                    (str(mount["destination"]), role)
                )
        lines.extend(["", "### Mounts"])
        for source in sorted(mount_access):
            access_text = "/".join(sorted(mount_access[source]))
            destination_text = ", ".join(
                f"{_code(destination)} ({_code(role)})"
                for destination, role in mount_destinations[source]
            )
            lines.append(
                f"- {_code(source)}: access={_code(access_text)}; "
                f"destinations/roles={destination_text}"
            )

        deployment = _mapping(
            component["deployment"],
            f"{component_path}.deployment",
        )
        rollback = _mapping(
            deployment["rollback"],
            f"{component_path}.deployment.rollback",
        )
        lines.extend(["", "### Deployment"])
        for label, record in (
            (
                "Active Compose",
                _mapping(deployment["compose_manifest"], "deployment.compose_manifest"),
            ),
            ("Active env", _mapping(deployment["env_file"], "deployment.env_file")),
            (
                "Rollback descriptor",
                _mapping(rollback["descriptor"], "deployment.rollback.descriptor"),
            ),
            (
                "Rollback Compose",
                _mapping(
                    rollback["compose_manifest"],
                    "deployment.rollback.compose_manifest",
                ),
            ),
            (
                "Rollback env",
                _mapping(rollback["env_file"], "deployment.rollback.env_file"),
            ),
        ):
            lines.append(
                f"- {label}: path={_code(record['path'])}; "
                f"sha256={_code(record['sha256'])}; mode={_code(record['mode'])}"
            )
        lines.append(f"- Rollback status: {_code(rollback['status'])}")
        rollback_command = _list(
            rollback["command"],
            f"{component_path}.deployment.rollback.command",
        )
        lines.append(
            "- Rollback argv: "
            + " ".join(_code(argument) for argument in rollback_command)
        )

        lines.extend(["", "### Files"])
        for index, raw_file in enumerate(
            _list(component["files"], f"{component_path}.files")
        ):
            file_entry = _mapping(raw_file, f"{component_path}.files[{index}]")
            lines.append(
                f"- {_code(file_entry['path'])} -> {_code(file_entry['container_path'])} "
                f"({_code(file_entry['container_role'])}): {_code(file_entry['sha256'])}"
            )

        heartbeat = component["heartbeat"]
        if heartbeat is None:
            lines.extend(["", "### Heartbeat", "", "- Not declared"])
        else:
            heartbeat_entry = _mapping(heartbeat, f"{component_path}.heartbeat")
            queues = ", ".join(
                _string_list(
                    heartbeat_entry["queues"],
                    f"{component_path}.heartbeat.queues",
                )
            )
            lines.extend(
                [
                    "",
                    "### Heartbeat",
                    "",
                    f"- Path: {_code(heartbeat_entry['path'])}",
                    f"- Schema: {_code(heartbeat_entry['schema'])}",
                    f"- Max age seconds: {_code(heartbeat_entry['max_age_seconds'])}",
                    f"- Queues: {_code(queues)}",
                ]
            )

    legacy = _mapping(validated["legacy"], "legacy")
    lines.extend(
        [
            "",
            "## Legacy",
            "",
            f"- Path: {_code(legacy['path'])}",
            f"- State: {_code(legacy['state'])}",
        ]
    )

    lines.extend(["", "## Public endpoints", ""])
    for index, raw_endpoint in enumerate(
        _list(validated["public_endpoints"], "public_endpoints")
    ):
        endpoint = _mapping(raw_endpoint, f"public_endpoints[{index}]")
        lines.append(
            f"- {_code(endpoint['name'])}: {_code(endpoint['url'])}; "
            f"status={_code(endpoint['status_code'])}; "
            f"body={_code(endpoint['body_sha256'])}"
        )

    lines.extend(["", "## Generic pointers", ""])
    for index, raw_pointer in enumerate(
        _list(validated["generic_pointers"], "generic_pointers")
    ):
        pointer = _mapping(raw_pointer, f"generic_pointers[{index}]")
        lines.append(
            f"- {_code(pointer['name'])}: {_code(pointer['path'])}; "
            f"{_code(pointer['sha256'])}"
        )
    return "\n".join(lines) + "\n"


_CONTAINER_HASH_SCRIPT: Final = (
    "import hashlib,pathlib,sys;"
    "print('sha256:'+hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())"
)
_ROUTING_HASH_SCRIPT: Final = (
    "import hashlib,os,sys;"
    "print('sha256:'+hashlib.sha256(os.environ[sys.argv[1]].encode('utf-8')).hexdigest())"
)


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
    if (
        not isinstance(completed, Completed)
        or type(completed.returncode) is not int
        or type(completed.stdout) is not bytes
        or type(completed.stderr) is not bytes
    ):
        errors.append(f"{failure} returned an invalid result")
        return None
    if completed.returncode != 0:
        errors.append(f"{failure} failed")
        return None
    return completed


def _stdout_line(
    completed: Completed,
    errors: list[str],
    failure: str,
) -> str | None:
    try:
        text = completed.stdout.decode("utf-8", errors="strict")
    except UnicodeError:
        errors.append(f"{failure} returned invalid UTF-8")
        return None
    lines = text.splitlines()
    if len(lines) != 1:
        errors.append(f"{failure} returned invalid output")
        return None
    return lines[0]


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


def _runtime_json(payload: bytes) -> object | None:
    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (AuthorityError, json.JSONDecodeError, UnicodeError, ValueError):
        return None


def _single_inspect_document(completed: Completed) -> Mapping[str, object] | None:
    payload = _runtime_json(completed.stdout)
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


def _path_is_allowed(path: PurePosixPath, repository_path: str) -> bool:
    roots = (PurePosixPath(repository_path), _WORKSPACE_ROOT)
    return any(_path_is_within(path, root) for root in roots)


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _file_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _open_anchored_parent(
    path: Path,
    repository_path: str,
) -> tuple[int, tuple[tuple[int, int], ...]]:
    candidate = PurePosixPath(str(path))
    if not candidate.is_absolute() or not _path_is_allowed(candidate, repository_path):
        raise OSError("path is outside allowed roots")

    descriptors: list[int] = []
    try:
        descriptors.append(os.open("/", _DIRECTORY_OPEN_FLAGS))
        identities = [_directory_identity(os.fstat(descriptors[-1]))]
        for component in candidate.parts[1:-1]:
            descriptors.append(
                os.open(
                    component,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=descriptors[-1],
                )
            )
            identities.append(_directory_identity(os.fstat(descriptors[-1])))
        parent_descriptor = descriptors.pop()
        return parent_descriptor, tuple(identities)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_anchored_regular_file(
    path: Path,
    repository_path: str,
) -> tuple[int, _AnchoredFileWitness] | None:
    parent_descriptor: int | None = None
    file_descriptor: int | None = None
    try:
        parent_descriptor, directories = _open_anchored_parent(
            path,
            repository_path,
        )
        file_descriptor = os.open(
            path.name,
            _REGULAR_FILE_OPEN_FLAGS,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(file_descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        witness = _AnchoredFileWitness(directories, _file_identity(metadata))
        result_descriptor = file_descriptor
        file_descriptor = None
        return result_descriptor, witness
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _digest_regular_file(
    path: Path,
    repository_path: str,
) -> tuple[str, _AnchoredFileWitness] | None:
    opened = _open_anchored_regular_file(path, repository_path)
    if opened is None:
        return None
    descriptor, witness = opened
    try:
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if _file_identity(os.fstat(descriptor)) != witness.file:
            return None
        return "sha256:" + digest.hexdigest(), witness
    finally:
        os.close(descriptor)


def _anchored_regular_file_matches(
    path: Path,
    repository_path: str,
    expected: _AnchoredFileWitness,
) -> bool:
    try:
        opened = _open_anchored_regular_file(path, repository_path)
    except OSError:
        return False
    if opened is None:
        return False
    descriptor, actual = opened
    try:
        return actual == expected
    finally:
        os.close(descriptor)


def _read_anchored_symlink(
    path: Path,
    repository_path: str,
) -> _AnchoredSymlinkWitness | None:
    parent_descriptor: int | None = None
    try:
        parent_descriptor, directories = _open_anchored_parent(
            path,
            repository_path,
        )
        initial_stat = os.lstat(path.name, dir_fd=parent_descriptor)
        if not stat.S_ISLNK(initial_stat.st_mode):
            return None
        target = os.readlink(path.name, dir_fd=parent_descriptor)
        final_stat = os.lstat(path.name, dir_fd=parent_descriptor)
        if (
            not stat.S_ISLNK(final_stat.st_mode)
            or _file_identity(final_stat) != _file_identity(initial_stat)
        ):
            return None
        return _AnchoredSymlinkWitness(
            directories,
            _file_identity(initial_stat),
            target,
        )
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _anchored_symlink_matches(
    path: Path,
    repository_path: str,
    expected: _AnchoredSymlinkWitness,
) -> bool:
    try:
        return _read_anchored_symlink(path, repository_path) == expected
    except OSError:
        return False


def _normalized_symlink_target(path: Path, target: str) -> PurePosixPath:
    combined = (
        target
        if posixpath.isabs(target)
        else posixpath.join(path.parent.as_posix(), target)
    )
    return PurePosixPath(posixpath.normpath(combined))


def _verify_git_authority(
    name: str,
    source: Mapping[str, object],
    repository_path: str,
    runner: Runner,
    errors: list[str],
) -> None:
    commit = str(source["commit"])
    expected_tree = str(source["tree"])
    ref = str(source["ref"])
    prefix = f"{name}:"
    git = ["git", "-C", repository_path]

    tree = _run_checked(
        runner,
        [*git, "rev-parse", f"{commit}^{{tree}}"],
        errors,
        f"{prefix} git tree",
    )
    if tree is not None:
        actual_tree = _stdout_line(tree, errors, f"{prefix} git tree")
        if actual_tree is not None and actual_tree != expected_tree:
            errors.append(f"{prefix} git tree mismatch")

    local = _run_checked(
        runner,
        [*git, "show-ref", "--verify", ref],
        errors,
        f"{prefix} local production ref",
    )
    if local is not None:
        actual_local = _stdout_line(local, errors, f"{prefix} local production ref")
        if actual_local is not None and actual_local != f"{commit} {ref}":
            errors.append(f"{prefix} local production ref mismatch")

    remote = _run_checked(
        runner,
        [*git, "ls-remote", "--heads", "origin", ref],
        errors,
        f"{prefix} remote production ref",
    )
    if remote is not None:
        actual_remote = _stdout_line(remote, errors, f"{prefix} remote production ref")
        if actual_remote is not None and actual_remote != f"{commit}\t{ref}":
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

    expected_health = container["health"]
    if expected_health is None:
        if state is None or "Health" in state:
            errors.append(f"{prefix} container health mismatch")
    else:
        health = _nested_mapping(state, "Health")
        if health is None or health.get("Status") != expected_health:
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

    expected_traefik = _mapping(
        container["traefik_labels"], "container.traefik_labels"
    )
    actual_traefik = (
        {key: value for key, value in labels.items() if key.startswith("traefik.")}
        if labels is not None
        else None
    )
    if actual_traefik != dict(expected_traefik):
        errors.append(f"{prefix} Traefik labels mismatch")


def _verify_heartbeat(
    name: str,
    heartbeat_value: object,
    errors: list[str],
) -> None:
    if heartbeat_value is None:
        return
    heartbeat = _mapping(heartbeat_value, f"components.{name}.heartbeat")
    path = Path(str(heartbeat["path"]))
    try:
        if path.is_symlink() or not path.is_file():
            errors.append(f"{name}: heartbeat file invalid")
            return
        payload = path.read_bytes()
    except OSError:
        errors.append(f"{name}: heartbeat file unavailable")
        return

    document = _runtime_json(payload)
    expected_fields = {
        "schema",
        "observed_at",
        "status",
        "failed_queues",
        "public_ingress_ready",
        "public_ingress_reason",
        "public_turn_capacity",
        "queues",
    }
    if not isinstance(document, Mapping) or set(document) != expected_fields:
        errors.append(f"{name}: heartbeat JSON invalid")
        return
    if document.get("schema") != heartbeat["schema"]:
        errors.append(f"{name}: heartbeat schema mismatch")
        return
    if document.get("status") != "healthy" or document.get("failed_queues") != []:
        errors.append(f"{name}: heartbeat status unhealthy")
        return
    if document.get("public_ingress_ready") is not True:
        errors.append(f"{name}: heartbeat public ingress not ready")
        return
    if document.get("public_ingress_reason") is not None:
        errors.append(f"{name}: heartbeat public ingress reason invalid")
        return
    capacity = document.get("public_turn_capacity")
    if type(capacity) is not int or capacity < 1:
        errors.append(f"{name}: heartbeat public turn capacity invalid")
        return

    observed_at = document.get("observed_at")
    if type(observed_at) is not str:
        errors.append(f"{name}: heartbeat timestamp invalid")
        return
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{name}: heartbeat timestamp invalid")
        return
    if (
        observed.tzinfo is None
        or observed.utcoffset() != timezone.utc.utcoffset(observed)
    ):
        errors.append(f"{name}: heartbeat timestamp invalid")
        return
    age_seconds = (datetime.now(timezone.utc) - observed).total_seconds()
    if age_seconds < 0 or age_seconds > int(heartbeat["max_age_seconds"]):
        errors.append(f"{name}: heartbeat timestamp not fresh")
        return

    queues = document.get("queues")
    expected_queues = set(_string_list(heartbeat["queues"], "heartbeat.queues"))
    if not isinstance(queues, Mapping) or set(queues) != expected_queues:
        errors.append(f"{name}: heartbeat queues mismatch")
        return
    if any(
        not isinstance(queues[queue], Mapping)
        or queues[queue].get("status") != "healthy"
        for queue in expected_queues
    ):
        errors.append(f"{name}: heartbeat queue unhealthy")


def _verify_files(
    name: str,
    component: Mapping[str, object],
    repository_path: str,
    runner: Runner,
    errors: list[str],
) -> None:
    prefix = f"{name}:"
    source = _mapping(component["source"], f"components.{name}.source")
    containers_by_role = {
        str(container["role"]): container
        for container in (
            _mapping(raw, f"components.{name}.container")
            for raw in _list(component["containers"], f"components.{name}.containers")
        )
    }
    git = ["git", "-C", repository_path]
    for raw_file in _list(component["files"], f"components.{name}.files"):
        file_entry = _mapping(raw_file, f"components.{name}.file")
        source_path = str(file_entry["path"])
        container_path = str(file_entry["container_path"])
        expected = str(file_entry["sha256"])
        source_file = _run_checked(
            runner,
            [*git, "show", f"{source['commit']}:{source_path}"],
            errors,
            f"{prefix} source file hash",
        )
        if source_file is not None and _digest_bytes(source_file.stdout) != expected:
            errors.append(f"{prefix} source file hash mismatch")

        container = containers_by_role[str(file_entry["container_role"])]
        container_file = _run_checked(
            runner,
            [
                "docker",
                "exec",
                str(container["name"]),
                "python",
                "-c",
                _CONTAINER_HASH_SCRIPT,
                container_path,
            ],
            errors,
            f"{prefix} container file hash",
        )
        if container_file is not None:
            actual = _stdout_line(
                container_file,
                errors,
                f"{prefix} container file hash",
            )
            if actual is not None and (
                not _SHA256_RE.fullmatch(actual) or actual != expected
            ):
                errors.append(f"{prefix} container file hash mismatch")


def _compose_config_command(
    compose_project: str,
    compose_path: str,
    env_path: str,
) -> list[str]:
    return [
        *_CLEAN_COMPOSE_PREFIX,
        "--project-name",
        compose_project,
        "--env-file",
        env_path,
        "-f",
        compose_path,
        "config",
        "--quiet",
    ]


def _verify_deployment(
    name: str,
    component: Mapping[str, object],
    repository_path: str,
    runner: Runner,
    errors: list[str],
) -> None:
    prefix = f"{name}:"
    records = dict(_deployment_records(component))
    for artifact_name, record in records.items():
        artifact_path = Path(str(record["path"]))
        try:
            digested = _digest_regular_file(artifact_path, repository_path)
        except (OSError, RuntimeError):
            digested = None
        if digested is None:
            errors.append(f"{prefix} {artifact_name} final file invalid")
            continue
        actual_digest, witness = digested
        if not _anchored_regular_file_matches(
            artifact_path,
            repository_path,
            witness,
        ):
            errors.append(f"{prefix} {artifact_name} changed during verification")
            continue
        try:
            confirmed = _digest_regular_file(artifact_path, repository_path)
        except (OSError, RuntimeError):
            confirmed = None
        if confirmed is None:
            errors.append(f"{prefix} {artifact_name} changed during verification")
            continue
        confirmed_digest, confirmed_witness = confirmed
        if confirmed_witness != witness or confirmed_digest != actual_digest:
            errors.append(f"{prefix} {artifact_name} changed during verification")
            continue
        actual_digest = confirmed_digest
        witness = confirmed_witness
        if actual_digest != record["sha256"]:
            errors.append(f"{prefix} {artifact_name} hash mismatch")
        if stat.S_IMODE(witness.file[2]) != int(str(record["mode"]), 8):
            errors.append(f"{prefix} {artifact_name} mode mismatch")

    deployment = _mapping(component["deployment"], "component.deployment")
    rollback = _mapping(deployment["rollback"], "component.deployment.rollback")
    first_container = _mapping(
        _list(component["containers"], "component.containers")[0],
        "component.container",
    )
    compose_project = str(first_container["compose_project"])
    for stage, selected in (
        ("active", deployment),
        ("rollback", rollback),
    ):
        compose = _mapping(selected["compose_manifest"], f"{stage}.compose_manifest")
        env_file = _mapping(selected["env_file"], f"{stage}.env_file")
        _run_checked(
            runner,
            _compose_config_command(
                compose_project,
                str(compose["path"]),
                str(env_file["path"]),
            ),
            errors,
            f"{prefix} {stage} compose validation",
        )


def _verify_routing(
    name: str,
    component: Mapping[str, object],
    runner: Runner,
    errors: list[str],
) -> None:
    routing_value = component["routing"]
    if routing_value is None:
        return
    routing = _mapping(routing_value, f"components.{name}.routing")
    containers_by_role = {
        str(container["role"]): container
        for container in (
            _mapping(raw, f"components.{name}.container")
            for raw in _list(component["containers"], f"components.{name}.containers")
        )
    }
    router = containers_by_role[str(routing["container_role"])]
    completed = _run_checked(
        runner,
        [
            "docker",
            "exec",
            str(router["name"]),
            "python",
            "-c",
            _ROUTING_HASH_SCRIPT,
            str(routing["variable"]),
        ],
        errors,
        f"{name}: routing target",
    )
    if completed is None:
        return
    actual = _stdout_line(completed, errors, f"{name}: routing target")
    if actual is not None and (
        not _SHA256_RE.fullmatch(actual) or actual != routing["target_hash"]
    ):
        errors.append(f"{name}: routing target mismatch")


def _verify_public_endpoints(
    endpoints: list[object],
    http_get: HttpGet,
    errors: list[str],
) -> None:
    for raw_endpoint in endpoints:
        endpoint = _mapping(raw_endpoint, "public_endpoint")
        response = _http_checked(
            http_get,
            str(endpoint["url"]),
            errors,
            "public endpoint",
        )
        if response is None:
            continue
        status, body = response
        if status != endpoint["status_code"]:
            errors.append("public endpoint status mismatch")
        if _digest_bytes(body) != endpoint["body_sha256"]:
            errors.append("public endpoint body hash mismatch")


def _verify_generic_pointers(
    pointers: list[object],
    repository_path: str,
    errors: list[str],
) -> None:
    for raw_pointer in pointers:
        pointer = _mapping(raw_pointer, "generic_pointer")
        path = Path(str(pointer["path"]))
        declared_target = pointer["symlink_target"]
        try:
            if declared_target is None:
                digested = _digest_regular_file(path, repository_path)
                if digested is None:
                    errors.append("generic pointer final file invalid")
                    continue
                actual_digest, file_witness = digested
                if not _anchored_regular_file_matches(
                    path,
                    repository_path,
                    file_witness,
                ):
                    errors.append("generic pointer changed during verification")
                    continue
            else:
                initial_symlink = _read_anchored_symlink(path, repository_path)
                if initial_symlink is None:
                    errors.append("generic pointer symlink mismatch")
                    continue
                declared_path = Path(str(declared_target))
                expected_target = PurePosixPath(str(declared_path))
                linked_target = _normalized_symlink_target(
                    path,
                    initial_symlink.target,
                )
                if linked_target != expected_target:
                    errors.append("generic pointer symlink target mismatch")
                    continue
                if not _path_is_allowed(expected_target, repository_path):
                    errors.append("generic pointer resolved outside allowed roots")
                    continue
                digested = _digest_regular_file(declared_path, repository_path)
                if digested is None:
                    errors.append("generic pointer final file invalid")
                    continue
                actual_digest, target_witness = digested
                if not _anchored_regular_file_matches(
                    declared_path,
                    repository_path,
                    target_witness,
                ):
                    errors.append("generic pointer target changed during verification")
                    continue
                if not _anchored_symlink_matches(
                    path,
                    repository_path,
                    initial_symlink,
                ):
                    errors.append("generic pointer symlink changed during verification")
                    continue
        except (OSError, RuntimeError):
            errors.append("generic pointer file unavailable")
            continue
        if actual_digest != pointer["sha256"]:
            errors.append("generic pointer hash mismatch")


def verify_manifest(
    manifest: Mapping[str, object],
    runner: Runner,
    http_get: HttpGet,
) -> list[str]:
    """Verify every declared authority boundary, returning sanitized failures."""

    validated = _validate_manifest(manifest)
    repository = _mapping(validated["repository"], "repository")
    repository_path = str(repository["path"])
    components = _mapping(validated["components"], "components")
    errors: list[str] = []
    for name in COMPONENT_ORDER:
        component = _mapping(components[name], f"components.{name}")
        source = _mapping(component["source"], f"components.{name}.source")
        image = _mapping(component["image"], f"components.{name}.image")
        _verify_git_authority(name, source, repository_path, runner, errors)
        _verify_image(name, image, runner, errors)
        for raw_container in _list(
            component["containers"], f"components.{name}.containers"
        ):
            container = _mapping(raw_container, f"components.{name}.container")
            _verify_container(name, image, container, runner, errors)
        _verify_files(name, component, repository_path, runner, errors)
        _verify_deployment(name, component, repository_path, runner, errors)
        _verify_heartbeat(name, component["heartbeat"], errors)
        _verify_routing(name, component, runner, errors)

    _verify_public_endpoints(
        _list(validated["public_endpoints"], "public_endpoints"),
        http_get,
        errors,
    )
    _verify_generic_pointers(
        _list(validated["generic_pointers"], "generic_pointers"),
        repository_path,
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


def _write_text_atomic(path: Path, value: str) -> None:
    temporary_path: Path | None = None
    directory_descriptor: int | None = None
    try:
        existing_stat = os.lstat(path)
    except FileNotFoundError:
        output_mode = 0o644
    else:
        if not stat.S_ISREG(existing_stat.st_mode):
            raise OSError("output path must be a regular file")
        output_mode = stat.S_IMODE(existing_stat.st_mode)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), output_mode)
            temporary.write(value)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            current_stat = os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(current_stat.st_mode):
                raise OSError("output path must be a regular file")
        os.replace(temporary_path, path)
        temporary_path = None
        directory_descriptor = os.open(path.parent, _DIRECTORY_OPEN_FLAGS)
        os.fsync(directory_descriptor)
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run the render or verify command without exposing boundary payloads."""

    arguments = _argument_parser().parse_args(argv)
    try:
        manifest = load_manifest(arguments.manifest)
        if arguments.command == "render":
            _write_text_atomic(arguments.output, render_markdown(manifest))
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
