"""Contract tests for the versioned, fail-closed runtime authority verifier."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import runtime_authority as authority
from scripts.runtime_authority import AuthorityError, load_manifest, render_markdown

COMPONENT_NAMES = ("ga", "test_contact", "ops")
QUEUE_NAMES = (
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
_COMPONENT_HEX = {"ga": "1", "test_contact": "1", "ops": "3"}
_COMPONENT_ROLES = {
    "ga": ("api", "worker", "router"),
    "test_contact": ("api", "worker", "router"),
    "ops": ("web",),
}
_ROUTING_VARIABLE = "TEST_CONTACT_SUBSCRIBER_ID"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ACTIVE_RUNTIME_MANIFEST = (
    "/home/ubuntu/workspace/agente-v2-control/ACTIVE_RUNTIME.json"
)
_VERIFY_RUNTIME_COMMAND = (
    "python3 scripts/runtime_authority.py verify --manifest "
    + _ACTIVE_RUNTIME_MANIFEST
)
_RUNTIME_HEADING = "## Runtime ativo (obrigatório)"
_FALSE_VERIFY_SUCCESS_LITERAL = "PASS agente-v2-active-runtime-v1"
_JSON_VERIFY_SUCCESS = '{"ok": true, "errors": []}'
_COMMON_AUTHORITY_TERMS = (
    "não inferir",
    "main",
    "nome de worktree",
    "tag genérica",
    "compose solto",
    "repo legado",
    "v3 fora de escopo",
    "ga",
    "teste isolado",
    "ops",
    "production/ga",
    "production/ops",
)


def _read_repository_document(relative_path: str) -> str:
    path = _REPOSITORY_ROOT / relative_path
    assert path.is_file(), f"missing documentation file: {relative_path}"
    return path.read_text(encoding="utf-8")


def _runtime_section(document: str) -> str:
    assert _RUNTIME_HEADING in document
    remainder = document.split(_RUNTIME_HEADING, 1)[1]
    return remainder.split("\n## ", 1)[0]


@pytest.mark.parametrize(
    ("relative_path", "historical_heading"),
    (
        ("AGENTS.md", "## Trilha histórica de refatoração"),
        ("README.md", "## Objetivo"),
        ("docs/refactor/README.md", "## Princípio"),
    ),
)
def test_documentation_entry_docs_put_runtime_authority_before_history(
    relative_path: str,
    historical_heading: str,
) -> None:
    document = _read_repository_document(relative_path)

    assert _RUNTIME_HEADING in document, f"{relative_path} omits the runtime heading"
    assert historical_heading in document
    assert document.index(_RUNTIME_HEADING) < document.index(historical_heading)
    runtime_section = _runtime_section(document)
    assert _ACTIVE_RUNTIME_MANIFEST in runtime_section
    assert _VERIFY_RUNTIME_COMMAND in runtime_section
    normalized = runtime_section.casefold()
    for term in _COMMON_AUTHORITY_TERMS:
        assert term in normalized, f"{relative_path} omits {term!r}"
    assert "sha256:" not in normalized
    assert re.search(r"\b[0-9a-f]{40}\b", normalized) is None


def test_documentation_runbook_is_ordered_fail_closed_and_operational() -> None:
    document = _read_repository_document("docs/operations/runtime-authority.md")
    normalized = document.casefold()

    assert _ACTIVE_RUNTIME_MANIFEST in document
    assert _VERIFY_RUNTIME_COMMAND in document
    for term in _COMMON_AUTHORITY_TERMS:
        assert term in normalized, f"runbook omits {term!r}"
    ordered_steps = (
        "### 1. read",
        "### 2. verify",
        "### 3. branch",
        "### 4. stop on drift",
    )
    positions = [normalized.index(step) for step in ordered_steps]
    assert positions == sorted(positions)
    for term in (
        "production-ga",
        "production-ops",
        "/readyz",
        "read-only",
        "estado separado",
        "deploy atômico",
        "rollback atômico",
        "atualização atômica",
        "sem segredos",
        "containers",
        "heartbeat opcional",
        "ponteiros locais",
    ):
        assert term in normalized, f"runbook omits {term!r}"


@pytest.mark.parametrize(
    ("relative_path", "historical_heading"),
    (
        (
            "docs/superpowers/specs/2026-09-05-v2-runtime-authority-design.md",
            "## problema",
        ),
        (
            "docs/superpowers/plans/2026-09-05-v2-runtime-authority.md",
            "### task 1:",
        ),
    ),
)
def test_documentation_design_and_plan_record_review_addendum(
    relative_path: str,
    historical_heading: str,
) -> None:
    document = _read_repository_document(relative_path)
    normalized = document.casefold()

    assert "## addendum" in normalized, f"{relative_path} omits the review addendum"
    assert historical_heading in normalized
    addendum_position = normalized.index("## addendum")
    assert addendum_position > normalized.index(historical_heading)
    addendum = normalized[addendum_position:]
    for term in (
        "histórico",
        "review",
        "production-ga",
        "production-ops",
        "containers",
        "heartbeat opcional",
        "ponteiros locais",
    ):
        assert term in addendum, f"{relative_path} addendum omits {term!r}"


@pytest.mark.parametrize(
    "relative_path",
    (
        "docs/superpowers/specs/2026-09-05-v2-runtime-authority-design.md",
        "docs/superpowers/plans/2026-09-05-v2-runtime-authority.md",
    ),
)
def test_documentation_historical_plan_and_design_open_with_authority_banner(
    relative_path: str,
) -> None:
    document = _read_repository_document(relative_path)
    first_block = document.split("\n## ", 1)[0].casefold()

    for term in (
        "documento histórico de execução",
        "autoridade normativa",
        "docs/operations/runtime-authority.md",
        _ACTIVE_RUNTIME_MANIFEST.casefold(),
        "addendum",
        "código atual",
        "prevalecem",
        "comandos históricos",
    ):
        assert term in first_block, f"{relative_path} first block omits {term!r}"


def test_documentation_task6_and_design_use_machine_readable_success_contract() -> None:
    plan = _read_repository_document(
        "docs/superpowers/plans/2026-09-05-v2-runtime-authority.md"
    )
    design = _read_repository_document(
        "docs/superpowers/specs/2026-09-05-v2-runtime-authority-design.md"
    )
    task6 = plan.split("### Task 6:", 1)[1].split("\n---", 1)[0]

    for relative_path, contract in (
        ("docs/superpowers/plans/2026-09-05-v2-runtime-authority.md", task6),
        (
            "docs/superpowers/specs/2026-09-05-v2-runtime-authority-design.md",
            design,
        ),
    ):
        normalized = contract.casefold()
        assert "--json" in contract, f"{relative_path} omits --json"
        assert "exit `0`" in normalized, f"{relative_path} omits exit 0"
        assert _JSON_VERIFY_SUCCESS in contract, (
            f"{relative_path} omits the stable JSON success object"
        )
        assert "`runtime authority: ok`" in normalized
        assert "não é api estável" in normalized

    assert _FALSE_VERIFY_SUCCESS_LITERAL not in plan
    assert _FALSE_VERIFY_SUCCESS_LITERAL not in design


def test_documentation_plan_addendum_closes_host_router_bootstrap_cycle() -> None:
    plan = _read_repository_document(
        "docs/superpowers/plans/2026-09-05-v2-runtime-authority.md"
    )
    addendum = plan.split("## Addendum", 1)[1].casefold()

    assert "### bootstrap sem circularidade" in addendum
    assert "não publique nem ative `/home/ubuntu/agents.md` antes" in addendum
    for prerequisite in (
        _ACTIVE_RUNTIME_MANIFEST.casefold(),
        "docs/operations/runtime-authority.md",
        "scripts/runtime_authority.py",
    ):
        assert prerequisite in addendum
    assert "read → verify" in addendum
    assert "exceção temporária" in addendum
    assert "v3" in addendum


@pytest.mark.parametrize(
    ("relative_path", "historical_heading"),
    (
        ("AGENTS.md", "## Trilha histórica de refatoração"),
        ("README.md", "## Snapshot histórico das fases de refatoração"),
        (
            "docs/refactor/README.md",
            "## Snapshot histórico das fases de refatoração",
        ),
    ),
)
def test_documentation_old_phases_are_unambiguous_historical_snapshots(
    relative_path: str,
    historical_heading: str,
) -> None:
    document = _read_repository_document(relative_path)
    normalized = document.casefold()

    assert historical_heading in document
    assert document.index(_RUNTIME_HEADING) < document.index(historical_heading)
    assert "somente para tarefas explicitamente de refatoração" in normalized
    assert (
        "nunca servem para escolher checkout nem para inferir o runtime ativo"
        in normalized
    )
    assert "fase ativa:" not in normalized


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _assert_routing_hash_script(raw_value: bytes) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            authority._ROUTING_HASH_SCRIPT,
            _ROUTING_VARIABLE,
        ],
        check=False,
        capture_output=True,
        env={_ROUTING_VARIABLE: raw_value.decode("utf-8")},
        text=False,
        timeout=10,
    )

    assert completed.returncode == 0, "routing hash script must exit successfully"
    assert raw_value not in completed.stdout, "routing hash stdout leaked the raw value"
    assert raw_value not in completed.stderr, "routing hash stderr leaked the raw value"
    assert completed.stdout == (_digest(raw_value) + "\n").encode("ascii")
    assert completed.stderr == b""


def _file_bytes(name: str) -> bytes:
    return f"print('synthetic {name}')\n".encode()


def _pointer_bytes(name: str) -> bytes:
    return f"agente-v2:{name}:synthetic\n".encode()


def _source_ref(name: str) -> str:
    if name in {"ga", "test_contact"}:
        return "refs/heads/production/ga"
    return "refs/heads/production/ops"


def _traefik_labels(name: str, role: str) -> dict[str, str]:
    if role not in {"router", "web"}:
        return {}
    slug = name.replace("_", "-")
    namespace = f"traefik.http.routers.{slug}"
    return {
        "traefik.enable": "true",
        f"{namespace}.priority": str({"ga": 100, "test_contact": 200, "ops": 50}[name]),
        f"{namespace}.rule": f"PathPrefix(`/v2/{slug}`)",
        f"{namespace}.service": f"{slug}-service",
    }


def _container(name: str, role: str) -> dict[str, Any]:
    slug = name.replace("_", "-")
    return {
        "role": role,
        "name": f"agente-v2-{slug}-{role}",
        "compose_project": f"agente-v2-{slug}",
        "compose_config": "/srv/agente-v2/compose.yml",
        "compose_service": f"{slug}-{role}",
        "status": "running",
        "health": None if role == "worker" else "healthy",
        "user": "65532:65532",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mounts": [
            {
                "source": f"/srv/agente-v2/{slug}/{role}/config",
                "destination": "/app/config",
                "read_only": True,
            }
        ],
        "traefik_labels": _traefik_labels(name, role),
    }


def _component(name: str, repository_path: Path) -> dict[str, Any]:
    marker = _COMPONENT_HEX[name]
    slug = name.replace("_", "-")
    file_bytes = _file_bytes(name)
    file_role = "web" if name == "ops" else "api"
    heartbeat: dict[str, Any] | None
    routing: dict[str, Any] | None
    if name == "ops":
        heartbeat = None
        routing = None
    else:
        heartbeat = {
            "path": str(repository_path / "heartbeats" / f"{slug}.json"),
            "schema": "v2-worker-heartbeat-v2",
            "max_age_seconds": 3600,
            "queues": list(QUEUE_NAMES),
        }
        routing = None
        if name == "test_contact":
            routing = {
                "container_role": "router",
                "variable": _ROUTING_VARIABLE,
                "target_hash": _digest(b"synthetic-routing-target"),
            }
    return {
        "source": {
            "commit": marker * 40,
            "tree": marker * 40,
            "ref": _source_ref(name),
        },
        "image": {
            "ref": f"registry.invalid/agente-v2-{slug}@sha256:{marker * 64}",
            "id": f"sha256:{marker * 64}",
            "revision": marker * 40,
        },
        "containers": [_container(name, role) for role in _COMPONENT_ROLES[name]],
        "files": [
            {
                "path": f"runtime/{name}.py",
                "container_path": f"/app/runtime/{name}.py",
                "container_role": file_role,
                "sha256": _digest(file_bytes),
            }
        ],
        "heartbeat": heartbeat,
        "routing": routing,
    }


def minimal_manifest(repository_path: Path | None = None) -> dict[str, Any]:
    repository_path = repository_path or Path("/home/ubuntu/workspace/agente-v2")
    release_target = repository_path / "releases" / "ACTIVE_RUNTIME.json"
    return {
        "schema": "agente-v2-active-runtime-v1",
        "generated_at": "2026-09-05T12:00:00Z",
        "repository": {"path": str(repository_path), "remote": "origin"},
        "components": {
            name: _component(name, repository_path) for name in COMPONENT_NAMES
        },
        "legacy": {
            "path": "/home/ubuntu/workspace/agente-v1",
            "state": "excluded",
        },
        "public_endpoints": [
            {
                "name": "public-readyz",
                "url": "https://runtime.invalid/readyz",
                "status_code": 200,
                "body_sha256": _digest(b"ready\r\n"),
            }
        ],
        "generic_pointers": [
            {
                "name": "active-markdown",
                "path": str(repository_path / "ACTIVE_RUNTIME.md"),
                "sha256": _digest(_pointer_bytes("active-markdown")),
                "symlink_target": None,
            },
            {
                "name": "active-json",
                "path": str(repository_path / "ACTIVE_RUNTIME.json"),
                "sha256": _digest(_pointer_bytes("active-json")),
                "symlink_target": str(release_target),
            },
        ],
        "verification": {"documented_variables": [_ROUTING_VARIABLE]},
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _heartbeat_document(*, observed_at: str | None = None) -> dict[str, Any]:
    return {
        "schema": "v2-worker-heartbeat-v2",
        "observed_at": observed_at
        or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "status": "healthy",
        "failed_queues": [],
        "public_ingress_ready": True,
        "public_ingress_reason": None,
        "public_turn_capacity": 1,
        "queues": {queue: {"status": "healthy"} for queue in QUEUE_NAMES},
    }


def materialized_manifest(tmp_path: Path) -> dict[str, Any]:
    repository_path = tmp_path / "repository"
    repository_path.mkdir(parents=True)
    manifest = minimal_manifest(repository_path)

    for name in ("ga", "test_contact"):
        heartbeat_path = Path(manifest["components"][name]["heartbeat"]["path"])
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path.write_text(json.dumps(_heartbeat_document()), encoding="utf-8")

    regular_pointer = manifest["generic_pointers"][0]
    Path(regular_pointer["path"]).write_bytes(_pointer_bytes(regular_pointer["name"]))

    symlink_pointer = manifest["generic_pointers"][1]
    symlink_target = Path(symlink_pointer["symlink_target"])
    symlink_target.parent.mkdir(parents=True)
    symlink_target.write_bytes(_pointer_bytes(symlink_pointer["name"]))
    Path(symlink_pointer["path"]).symlink_to(symlink_target)
    return manifest


def _container_for(
    manifest: dict[str, Any], component_name: str, role: str
) -> dict[str, Any]:
    return next(
        container
        for container in manifest["components"][component_name]["containers"]
        if container["role"] == role
    )


def test_load_manifest_accepts_real_closed_topology(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    assert load_manifest(path) == manifest
    assert [len(manifest["components"][name]["containers"]) for name in COMPONENT_NAMES] == [
        3,
        3,
        1,
    ]
    assert _container_for(manifest, "ga", "worker")["health"] is None
    assert manifest["components"]["ops"]["heartbeat"] is None
    assert (
        manifest["components"]["test_contact"]["source"]["ref"]
        == manifest["components"]["ga"]["source"]["ref"]
    )


@pytest.mark.parametrize(
    "missing",
    (
        "schema",
        "generated_at",
        "repository",
        "components",
        "legacy",
        "public_endpoints",
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
    manifest["components"]["ga"]["containers"][0]["privileged"] = False
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


@pytest.mark.parametrize(
    "unsafe_key",
    ("api_key", "person@example.invalid", "+55 11 99999-9999"),
)
def test_manifest_rejects_sensitive_email_and_phone_shaped_keys_without_echoing_them(
    tmp_path: Path, unsafe_key: str
) -> None:
    manifest = minimal_manifest()
    manifest[unsafe_key] = "synthetic"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="forbidden key") as raised:
        load_manifest(path)
    assert unsafe_key not in str(raised.value)


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

    with pytest.raises(AuthorityError, match="forbidden string") as raised:
        load_manifest(path)
    assert unsafe not in str(raised.value)


def test_documented_sensitive_variable_is_allowed_only_at_authorized_paths(
    tmp_path: Path,
) -> None:
    manifest = minimal_manifest()
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)
    assert load_manifest(path) == manifest

    manifest["legacy"]["path"] = f"/srv/{_ROUTING_VARIABLE}"
    _write_manifest(path, manifest)
    with pytest.raises(AuthorityError, match="forbidden string"):
        load_manifest(path)


def test_routing_variable_must_be_documented(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["verification"]["documented_variables"] = ["OTHER_VARIABLE"]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="routing variable"):
        load_manifest(path)


def test_manifest_rejects_duplicate_json_keys_without_echoing_key(tmp_path: Path) -> None:
    path = tmp_path / "ACTIVE_RUNTIME.json"
    path.write_text(
        '{"person@example.invalid":"one","person@example.invalid":"two"}',
        encoding="utf-8",
    )

    with pytest.raises(AuthorityError, match="duplicate key") as raised:
        load_manifest(path)
    assert "person@example.invalid" not in str(raised.value)


def test_manifest_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "ACTIVE_RUNTIME.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(AuthorityError, match="JSON object"):
        load_manifest(path)


@pytest.mark.parametrize(
    "root_identity",
    ("root", " ROOT ", "0", " 0 ", "0:0", "0:65532", " 0 : 65532 ", "", "   "),
)
def test_manifest_rejects_all_root_and_empty_user_variants(
    tmp_path: Path, root_identity: str
) -> None:
    manifest = minimal_manifest()
    _container_for(manifest, "ga", "api")["user"] = root_identity
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="non-root"):
        load_manifest(path)


@pytest.mark.parametrize("component_name", ("ga", "test_contact"))
@pytest.mark.parametrize("missing_role", ("api", "worker", "router"))
def test_ga_and_test_require_exact_api_worker_router_roles(
    tmp_path: Path, component_name: str, missing_role: str
) -> None:
    manifest = minimal_manifest()
    component = manifest["components"][component_name]
    component["containers"] = [
        container
        for container in component["containers"]
        if container["role"] != missing_role
    ]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="container roles"):
        load_manifest(path)


def test_ops_requires_exact_single_web_container(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["ops"]["containers"].append(_container("ops", "worker"))
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="container roles"):
        load_manifest(path)


def test_manifest_rejects_fabricated_ops_heartbeat(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["ops"]["heartbeat"] = dict(
        manifest["components"]["ga"]["heartbeat"]
    )
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="ops heartbeat"):
        load_manifest(path)


@pytest.mark.parametrize("component_name", ("ga", "test_contact"))
def test_ga_and_test_require_a_heartbeat_object(
    tmp_path: Path, component_name: str
) -> None:
    manifest = minimal_manifest()
    manifest["components"][component_name]["heartbeat"] = None
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="heartbeat"):
        load_manifest(path)


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate", "reordered"))
def test_manifest_requires_exact_ordered_ten_queue_universe(
    tmp_path: Path, mutation: str
) -> None:
    manifest = minimal_manifest()
    queues = manifest["components"]["ga"]["heartbeat"]["queues"]
    if mutation == "missing":
        queues.pop()
    elif mutation == "extra":
        queues.append("extra")
    elif mutation == "duplicate":
        queues[-1] = queues[0]
    elif mutation == "reordered":
        queues[0], queues[1] = queues[1], queues[0]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="queues"):
        load_manifest(path)


@pytest.mark.parametrize("missing_suffix", ("rule", "service"))
def test_priority_requires_matching_router_rule_and_service(
    tmp_path: Path, missing_suffix: str
) -> None:
    manifest = minimal_manifest()
    labels = _container_for(manifest, "ga", "router")["traefik_labels"]
    label = next(key for key in labels if key.endswith(f".{missing_suffix}"))
    del labels[label]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="priority"):
        load_manifest(path)


def test_each_component_requires_at_least_one_traefik_labeled_container(
    tmp_path: Path,
) -> None:
    manifest = minimal_manifest()
    for container in manifest["components"]["ga"]["containers"]:
        container["traefik_labels"] = {}
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="Traefik"):
        load_manifest(path)


def test_source_ref_must_be_full_local_production_ref(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["ga"]["source"]["ref"] = "production/ga"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="production ref"):
        load_manifest(path)


@pytest.mark.parametrize("outside_field", ("path", "symlink_target"))
def test_manifest_rejects_pointer_paths_outside_closed_roots(
    tmp_path: Path, outside_field: str
) -> None:
    manifest = minimal_manifest()
    pointer = manifest["generic_pointers"][1]
    pointer[outside_field] = "/tmp/outside-runtime-authority"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="allowed roots"):
        load_manifest(path)


def test_default_runner_preserves_raw_bytes_and_applies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        observed["args"] = args
        observed.update(kwargs)
        return SimpleNamespace(returncode=7, stdout=b"out\r\n", stderr=b"err\xff")

    monkeypatch.setattr(authority.subprocess, "run", fake_run)

    completed = authority._default_runner(["safe-command", "argument"])

    assert completed == authority.Completed(7, b"out\r\n", b"err\xff")
    assert observed == {
        "args": ["safe-command", "argument"],
        "capture_output": True,
        "check": False,
        "timeout": 10.0,
    }


def test_render_uses_fixed_component_and_container_order() -> None:
    manifest = minimal_manifest()
    manifest["components"] = {
        name: manifest["components"][name]
        for name in ("ops", "ga", "test_contact")
    }

    rendered = render_markdown(manifest)

    assert rendered.index("## GA") < rendered.index("## Test contact")
    assert rendered.index("## Test contact") < rendered.index("## Ops")
    assert rendered.index("`api`") < rendered.index("`worker`")
    assert rendered.index("`worker`") < rendered.index("`router`")


def test_render_omits_routing_variable_and_target_hash() -> None:
    manifest = minimal_manifest()
    routing = manifest["components"]["test_contact"]["routing"]

    rendered = render_markdown(manifest)

    assert routing["target_hash"] not in rendered
    assert routing["variable"] not in rendered


def test_render_emits_pointer_name_path_and_hash_only() -> None:
    manifest = minimal_manifest()

    rendered = render_markdown(manifest)

    for pointer in manifest["generic_pointers"]:
        assert pointer["name"] in rendered
        assert pointer["path"] in rendered
        assert pointer["sha256"] in rendered
        if pointer["symlink_target"] is not None:
            assert pointer["symlink_target"] not in rendered


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
        self.routing_hashes: dict[tuple[str, str], str] = {}
        self.http_responses: dict[str, tuple[int, bytes]] = {}
        self.repository_path = manifest["repository"]["path"]

        for name in COMPONENT_NAMES:
            component = manifest["components"][name]
            source = component["source"]
            image = component["image"]
            ref = source["ref"]
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
            containers_by_role: dict[str, dict[str, Any]] = {}
            for container in component["containers"]:
                containers_by_role[container["role"]] = container
                labels = {
                    "com.docker.compose.project": container["compose_project"],
                    "com.docker.compose.project.config_files": container[
                        "compose_config"
                    ],
                    "com.docker.compose.service": container["compose_service"],
                    **container["traefik_labels"],
                }
                state: dict[str, Any] = {"Status": container["status"]}
                if container["health"] is not None:
                    state["Health"] = {"Status": container["health"]}
                self.containers[container["name"]] = {
                    "Image": image["id"],
                    "Config": {
                        "Image": image["ref"],
                        "User": container["user"],
                        "Env": ["RAW_ROUTING_VALUE_SHOULD_NOT_LEAK"],
                        "Labels": labels,
                    },
                    "State": state,
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
                self.git_files[(source["commit"], file_entry["path"])] = _file_bytes(
                    name
                )
                container = containers_by_role[file_entry["container_role"]]
                self.container_files[
                    (container["name"], file_entry["container_path"])
                ] = file_entry["sha256"]
            routing = component["routing"]
            if routing is not None:
                container = containers_by_role[routing["container_role"]]
                self.routing_hashes[(container["name"], routing["variable"])] = routing[
                    "target_hash"
                ]

        for endpoint in manifest["public_endpoints"]:
            self.http_responses[endpoint["url"]] = (200, b"ready\r\n")

    def _git_prefix(self) -> tuple[str, str, str]:
        return ("git", "-C", self.repository_path)

    def runner(self, raw_args: Any) -> authority.Completed:
        assert type(raw_args) is list
        args = tuple(raw_args)
        self.calls.append(args)
        if args in self.command_exceptions:
            raise self.command_exceptions[args]
        if args in self.command_failures:
            return self.command_failures[args]

        git_prefix = self._git_prefix()
        if args[:4] == (*git_prefix, "rev-parse") and len(args) == 5:
            commit = args[4].removesuffix("^{tree}")
            return authority.Completed(0, (self.trees[commit] + "\n").encode(), b"")
        if args[:5] == (*git_prefix, "show-ref", "--verify") and len(args) == 6:
            ref = args[5]
            return authority.Completed(
                0, f"{self.local_refs[ref]} {ref}\n".encode(), b""
            )
        if args[:6] == (*git_prefix, "ls-remote", "--heads", "origin") and len(args) == 7:
            ref = args[6]
            return authority.Completed(
                0, f"{self.remote_refs[ref]}\t{ref}\n".encode(), b""
            )
        if args[:4] == (*git_prefix, "show") and len(args) == 5:
            commit, path = args[4].split(":", 1)
            return authority.Completed(0, self.git_files[(commit, path)], b"")
        if args[:3] == ("docker", "image", "inspect") and len(args) == 4:
            return authority.Completed(0, json.dumps([self.images[args[3]]]).encode(), b"")
        if args[:2] == ("docker", "inspect") and len(args) == 3:
            return authority.Completed(
                0, json.dumps([self.containers[args[2]]]).encode(), b""
            )
        if (
            args[:2] == ("docker", "exec")
            and len(args) == 7
            and args[3:5] == ("python", "-c")
        ):
            key = (args[2], args[6])
            if key in self.container_files:
                output = self.container_files[key]
            elif key in self.routing_hashes:
                output = self.routing_hashes[key]
            else:
                raise AssertionError(f"unexpected docker exec target: {key!r}")
            return authority.Completed(0, (output + "\n").encode(), b"")
        raise AssertionError(f"unexpected verifier command: {args!r}")

    def http_get(self, url: str) -> tuple[int, bytes]:
        self.http_calls.append(url)
        if url in self.http_exceptions:
            raise self.http_exceptions[url]
        return self.http_responses[url]


def test_verify_manifest_checks_every_declared_authority_surface(
    tmp_path: Path,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert errors == []
    git_prefix = ("git", "-C", manifest["repository"]["path"])
    assert all(call[:3] == git_prefix for call in fake.calls if call[0] == "git")
    for name in COMPONENT_NAMES:
        component = manifest["components"][name]
        source = component["source"]
        image = component["image"]
        ref = source["ref"]
        assert (*git_prefix, "rev-parse", f"{source['commit']}^{{tree}}") in fake.calls
        assert (*git_prefix, "show-ref", "--verify", ref) in fake.calls
        assert (*git_prefix, "ls-remote", "--heads", "origin", ref) in fake.calls
        assert ("docker", "image", "inspect", image["ref"]) in fake.calls
        for container in component["containers"]:
            assert ("docker", "inspect", container["name"]) in fake.calls
        for file_entry in component["files"]:
            assert (
                *git_prefix,
                "show",
                f"{source['commit']}:{file_entry['path']}",
            ) in fake.calls
            container = _container_for(manifest, name, file_entry["container_role"])
            assert (
                "docker",
                "exec",
                container["name"],
                "python",
                "-c",
                authority._CONTAINER_HASH_SCRIPT,
                file_entry["container_path"],
            ) in fake.calls
    routing = manifest["components"]["test_contact"]["routing"]
    router = _container_for(manifest, "test_contact", "router")
    assert (
        "docker",
        "exec",
        router["name"],
        "python",
        "-c",
        authority._ROUTING_HASH_SCRIPT,
        routing["variable"],
    ) in fake.calls
    assert fake.http_calls == [manifest["public_endpoints"][0]["url"]]


def test_verify_source_hash_preserves_crlf_bytes(tmp_path: Path) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    component = manifest["components"]["ga"]
    file_entry = component["files"][0]
    crlf_bytes = b"first\r\nsecond\r\n"
    file_entry["sha256"] = _digest(crlf_bytes)
    fake.git_files[(component["source"]["commit"], file_entry["path"])] = crlf_bytes
    api = _container_for(manifest, "ga", "api")
    fake.container_files[(api["name"], file_entry["container_path"])] = file_entry[
        "sha256"
    ]

    assert authority.verify_manifest(manifest, fake.runner, fake.http_get) == []
    assert file_entry["sha256"] != _digest(crlf_bytes.decode().replace("\r\n", "\n").encode())


@pytest.mark.parametrize(
    ("surface", "expected_error"),
    (
        ("tree", "git tree"),
        ("local_ref", "local production ref"),
        ("missing_ref", "local production ref failed"),
        ("remote_ref", "remote production ref"),
        ("missing_image", "image inspect failed"),
        ("image_id", "image id"),
        ("image_revision", "OCI revision"),
        ("missing_container", "container inspect failed"),
        ("container_image", "container image id"),
        ("container_ref", "container image ref"),
        ("compose_project", "compose project"),
        ("compose_config", "compose config"),
        ("compose_service", "compose service"),
        ("status", "container status"),
        ("health", "container health"),
        ("worker_health", "container health"),
        ("user", "container user"),
        ("read_only", "read-only rootfs"),
        ("cap_drop", "cap-drop"),
        ("security_opt", "security options"),
        ("mounts", "mounts"),
        ("traefik", "Traefik labels"),
        ("git_file", "source file hash"),
        ("container_file", "container file hash"),
        ("missing_heartbeat", "heartbeat"),
        ("endpoint_status", "public endpoint status"),
        ("endpoint_body", "public endpoint body"),
        ("pointer", "generic pointer hash"),
        ("routing", "routing target"),
    ),
)
def test_verify_manifest_fails_closed_on_mismatch(
    tmp_path: Path, surface: str, expected_error: str
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    component = manifest["components"]["ga"]
    source = component["source"]
    image = component["image"]
    api = _container_for(manifest, "ga", "api")
    worker = _container_for(manifest, "ga", "worker")
    ref = source["ref"]
    container_doc = fake.containers[api["name"]]
    failed = authority.Completed(
        1, b"RAW_ROUTING_VALUE_SHOULD_NOT_LEAK", b"person@example.invalid"
    )

    if surface == "tree":
        fake.trees[source["commit"]] = "f" * 40
    elif surface == "local_ref":
        fake.local_refs[ref] = "f" * 40
    elif surface == "missing_ref":
        fake.command_failures[
            ("git", "-C", fake.repository_path, "show-ref", "--verify", ref)
        ] = failed
    elif surface == "remote_ref":
        fake.remote_refs[ref] = "f" * 40
    elif surface == "missing_image":
        fake.command_failures[("docker", "image", "inspect", image["ref"])] = failed
    elif surface == "image_id":
        fake.images[image["ref"]]["Id"] = "sha256:" + "f" * 64
    elif surface == "image_revision":
        fake.images[image["ref"]]["Config"]["Labels"][
            "org.opencontainers.image.revision"
        ] = "f" * 40
    elif surface == "missing_container":
        fake.command_failures[("docker", "inspect", api["name"])] = failed
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
        del container_doc["State"]["Health"]
    elif surface == "worker_health":
        fake.containers[worker["name"]]["State"]["Health"] = {"Status": "healthy"}
    elif surface == "user":
        container_doc["Config"]["User"] = "0"
    elif surface == "read_only":
        container_doc["HostConfig"]["ReadonlyRootfs"] = False
    elif surface == "cap_drop":
        container_doc["HostConfig"]["CapDrop"] = []
    elif surface == "security_opt":
        container_doc["HostConfig"]["SecurityOpt"] = []
    elif surface == "mounts":
        container_doc["Mounts"] = []
    elif surface == "traefik":
        router = _container_for(manifest, "ga", "router")
        router_doc = fake.containers[router["name"]]
        priority = next(
            key
            for key in router_doc["Config"]["Labels"]
            if key.endswith(".priority")
        )
        router_doc["Config"]["Labels"][priority] = "1"
    elif surface == "git_file":
        fake.git_files[(source["commit"], component["files"][0]["path"])] = b"wrong"
    elif surface == "container_file":
        file_entry = component["files"][0]
        fake.container_files[(api["name"], file_entry["container_path"])] = (
            "sha256:" + "f" * 64
        )
    elif surface == "missing_heartbeat":
        Path(component["heartbeat"]["path"]).unlink()
    elif surface == "endpoint_status":
        endpoint = manifest["public_endpoints"][0]
        fake.http_responses[endpoint["url"]] = (503, b"ready\r\n")
    elif surface == "endpoint_body":
        endpoint = manifest["public_endpoints"][0]
        fake.http_responses[endpoint["url"]] = (200, b"wrong")
    elif surface == "pointer":
        Path(manifest["generic_pointers"][0]["path"]).write_bytes(b"wrong")
    elif surface == "routing":
        routing = manifest["components"]["test_contact"]["routing"]
        router = _container_for(manifest, "test_contact", "router")
        fake.routing_hashes[(router["name"], routing["variable"])] = (
            "sha256:" + "f" * 64
        )
    else:  # pragma: no cover - guards the test matrix itself
        raise AssertionError(surface)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any(expected_error in error for error in errors), errors
    joined = "\n".join(errors)
    assert "RAW_ROUTING_VALUE_SHOULD_NOT_LEAK" not in joined
    assert "person@example.invalid" not in joined


def test_heartbeat_verification_accepts_real_v2_public_ingress_envelope(
    tmp_path: Path,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert errors == []


@pytest.mark.parametrize("component_name", ("ga", "test_contact"))
def test_heartbeat_verification_accepts_scaled_public_turn_capacity(
    tmp_path: Path,
    component_name: str,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    heartbeat_path = Path(manifest["components"][component_name]["heartbeat"]["path"])
    document = _heartbeat_document()
    document["public_turn_capacity"] = 3
    heartbeat_path.write_text(json.dumps(document), encoding="utf-8")

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert errors == []


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("missing_public_field", "ga: heartbeat JSON invalid"),
        ("extra_public_field", "ga: heartbeat JSON invalid"),
        ("ready_false", "ga: heartbeat public ingress not ready"),
        ("ready_non_bool", "ga: heartbeat public ingress not ready"),
        ("reason_non_null", "ga: heartbeat public ingress reason invalid"),
        ("reason_invalid", "ga: heartbeat public ingress reason invalid"),
        ("capacity_bool", "ga: heartbeat public turn capacity invalid"),
        ("capacity_zero", "ga: heartbeat public turn capacity invalid"),
        ("capacity_negative", "ga: heartbeat public turn capacity invalid"),
    ),
)
def test_heartbeat_public_ingress_contract_fails_closed_and_sanitized(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    heartbeat_path = Path(manifest["components"]["ga"]["heartbeat"]["path"])
    document = _heartbeat_document()
    leaked_value = "person@example.invalid"

    if mutation == "missing_public_field":
        del document["public_turn_capacity"]
    elif mutation == "extra_public_field":
        document["public_ingress_details"] = leaked_value
    elif mutation == "ready_false":
        document["public_ingress_ready"] = False
    elif mutation == "ready_non_bool":
        document["public_ingress_ready"] = 1
    elif mutation == "reason_non_null":
        document["public_ingress_reason"] = leaked_value
    elif mutation == "reason_invalid":
        document["public_ingress_reason"] = {"detail": leaked_value}
    elif mutation == "capacity_bool":
        document["public_turn_capacity"] = True
    elif mutation == "capacity_zero":
        document["public_turn_capacity"] = 0
    elif mutation == "capacity_negative":
        document["public_turn_capacity"] = -1
    else:  # pragma: no cover - guards the test matrix itself
        raise AssertionError(mutation)
    heartbeat_path.write_text(json.dumps(document), encoding="utf-8")

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert expected_error in errors
    joined = "\n".join(errors)
    assert leaked_value not in joined
    assert json.dumps(document) not in joined


@pytest.mark.parametrize(
    "mutation",
    (
        "invalid_json",
        "wrong_schema",
        "non_utc",
        "stale",
        "future",
        "unhealthy",
        "failed_queue",
        "missing_queue",
        "extra_queue",
        "queue_unhealthy",
        "symlink",
    ),
)
def test_heartbeat_verification_is_closed_fresh_and_sanitized(
    tmp_path: Path, mutation: str
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    heartbeat_path = Path(manifest["components"]["ga"]["heartbeat"]["path"])
    document = _heartbeat_document()
    leaked_timestamp = "2040-01-02T03:04:05Z"

    if mutation == "invalid_json":
        heartbeat_path.write_bytes(b"person@example.invalid")
    elif mutation == "wrong_schema":
        document["schema"] = "wrong"
    elif mutation == "non_utc":
        document["observed_at"] = "2026-09-05T12:00:00+01:00"
    elif mutation == "stale":
        document["observed_at"] = (
            datetime.now(UTC) - timedelta(hours=2)
        ).isoformat()
    elif mutation == "future":
        document["observed_at"] = leaked_timestamp
    elif mutation == "unhealthy":
        document["status"] = "person@example.invalid"
    elif mutation == "failed_queue":
        document["failed_queues"] = [QUEUE_NAMES[0]]
    elif mutation == "missing_queue":
        del document["queues"][QUEUE_NAMES[0]]
    elif mutation == "extra_queue":
        document["queues"]["extra"] = {"status": "healthy"}
    elif mutation == "queue_unhealthy":
        document["queues"][QUEUE_NAMES[0]]["status"] = "person@example.invalid"
    elif mutation == "symlink":
        heartbeat_path.unlink()
        target = heartbeat_path.with_suffix(".target")
        target.write_text(json.dumps(document), encoding="utf-8")
        heartbeat_path.symlink_to(target)
    if mutation not in {"invalid_json", "symlink"}:
        heartbeat_path.write_text(json.dumps(document), encoding="utf-8")

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any("ga: heartbeat" in error for error in errors), errors
    joined = "\n".join(errors)
    assert "person@example.invalid" not in joined
    assert leaked_timestamp not in joined
    assert json.dumps(document) not in joined


@pytest.mark.parametrize("mutation", ("broken", "wrong_target", "directory", "regular_is_link"))
def test_generic_pointer_files_fail_closed(
    tmp_path: Path, mutation: str
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    regular = manifest["generic_pointers"][0]
    linked = manifest["generic_pointers"][1]

    if mutation == "broken":
        Path(linked["symlink_target"]).unlink()
    elif mutation == "wrong_target":
        wrong_target = Path(manifest["repository"]["path"]) / "releases" / "wrong.json"
        wrong_target.write_bytes(_pointer_bytes(linked["name"]))
        linked["symlink_target"] = str(wrong_target)
    elif mutation == "directory":
        path = Path(regular["path"])
        path.unlink()
        path.mkdir()
    elif mutation == "regular_is_link":
        path = Path(regular["path"])
        path.unlink()
        path.symlink_to(Path(linked["symlink_target"]))

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any("generic pointer" in error for error in errors), errors


def test_generic_pointer_hashing_uses_nofollow_file_descriptors_and_short_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    regular_path = Path(manifest["generic_pointers"][0]["path"])
    symlink_path = Path(manifest["generic_pointers"][1]["path"])
    declared_target = Path(manifest["generic_pointers"][1]["symlink_target"])
    open_calls: list[tuple[str, int, int | None, Path | None]] = []
    lstat_calls: list[tuple[str, int | None]] = []
    readlink_calls: list[tuple[str, int | None]] = []
    tracked_fds: set[int] = set()
    read_chunks: list[bytes] = []
    real_open = os.open
    real_close = os.close
    real_lstat = os.lstat
    real_readlink = os.readlink
    real_read = os.read

    def recording_open(
        raw_path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is None:
            fd = real_open(raw_path, flags, mode)
            parent = None
        else:
            fd = real_open(raw_path, flags, mode, dir_fd=dir_fd)
            parent = Path(real_readlink(f"/proc/self/fd/{dir_fd}"))
        raw_text = os.fspath(raw_path)
        open_calls.append((raw_text, flags, dir_fd, parent))
        if not flags & os.O_DIRECTORY and raw_text in {
            regular_path.name,
            declared_target.name,
        }:
            tracked_fds.add(fd)
        return fd

    def recording_close(fd: int) -> None:
        tracked_fds.discard(fd)
        real_close(fd)

    def recording_lstat(
        raw_path: str | os.PathLike[str],
        *,
        dir_fd: int | None = None,
    ) -> os.stat_result:
        lstat_calls.append((os.fspath(raw_path), dir_fd))
        if dir_fd is None:
            return real_lstat(raw_path)
        return real_lstat(raw_path, dir_fd=dir_fd)

    def recording_readlink(
        raw_path: str | os.PathLike[str],
        *,
        dir_fd: int | None = None,
    ) -> str:
        readlink_calls.append((os.fspath(raw_path), dir_fd))
        if dir_fd is None:
            return real_readlink(raw_path)
        return real_readlink(raw_path, dir_fd=dir_fd)

    def short_read(fd: int, size: int) -> bytes:
        payload = real_read(fd, min(size, 3))
        if fd in tracked_fds:
            read_chunks.append(payload)
        return payload

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "close", recording_close)
    monkeypatch.setattr(os, "lstat", recording_lstat)
    monkeypatch.setattr(os, "readlink", recording_readlink)
    monkeypatch.setattr(os, "read", short_read)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert errors == []
    directory_flags = (
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    )
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    assert any(
        raw_path == "/" and flags == directory_flags and dir_fd is None
        for raw_path, flags, dir_fd, _parent in open_calls
    )
    assert all(
        dir_fd is not None
        for raw_path, _flags, dir_fd, _parent in open_calls
        if raw_path != "/"
    )
    assert all(
        flags in {directory_flags, file_flags}
        for _raw_path, flags, _dir_fd, _parent in open_calls
    )
    opened_files = {
        (parent, raw_path)
        for raw_path, flags, _dir_fd, parent in open_calls
        if flags == file_flags
    }
    assert (regular_path.parent, regular_path.name) in opened_files
    assert (declared_target.parent, declared_target.name) in opened_files
    assert (symlink_path.parent, symlink_path.name) not in opened_files
    assert len(lstat_calls) >= 2
    assert len(readlink_calls) >= 2
    assert all(
        raw_path == symlink_path.name and dir_fd is not None
        for raw_path, dir_fd in [*lstat_calls, *readlink_calls]
    )
    assert len([chunk for chunk in read_chunks if chunk]) > 2
    assert read_chunks.count(b"") == 2


def test_generic_pointer_symlink_replacement_during_hashing_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    linked = manifest["generic_pointers"][1]
    pointer_path = Path(linked["path"])
    declared_target = Path(linked["symlink_target"])
    outside_target = tmp_path / "outside-runtime-authority.json"
    outside_target.write_bytes(_pointer_bytes(linked["name"]))
    real_open = os.open
    real_readlink = os.readlink
    replaced = False

    def replace_pointer_during_target_open(
        raw_path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        raw_text = os.fspath(raw_path)
        anchored_parent = (
            Path(real_readlink(f"/proc/self/fd/{dir_fd}"))
            if dir_fd is not None
            else None
        )
        opens_declared_target = (
            dir_fd is None and Path(raw_text) == declared_target
        ) or (
            anchored_parent == declared_target.parent
            and raw_text == declared_target.name
            and not flags & os.O_DIRECTORY
        )
        if opens_declared_target and not replaced:
            pointer_path.unlink()
            pointer_path.symlink_to(outside_target)
            replaced = True
        if dir_fd is None:
            return real_open(raw_path, flags, mode)
        return real_open(raw_path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_pointer_during_target_open)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert replaced, "race hook must replace the declared pointer"
    assert pointer_path.resolve(strict=True) == outside_target
    assert "generic pointer symlink changed during verification" in errors


def test_generic_pointer_intermediate_directory_swap_during_open_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    linked = manifest["generic_pointers"][1]
    pointer_path = Path(linked["path"])
    declared_target = Path(linked["symlink_target"])
    target_parent = declared_target.parent
    displaced_parent = target_parent.with_name(f"{target_parent.name}-original")
    outside_parent = tmp_path / "outside-releases"
    outside_parent.mkdir()
    outside_target = outside_parent / declared_target.name
    outside_target.write_bytes(declared_target.read_bytes())
    initial_pointer_stat = os.lstat(pointer_path)
    initial_pointer_target = os.readlink(pointer_path)
    real_open = os.open
    real_readlink = os.readlink
    swapped = False

    def swap_intermediate_directory_during_target_open(
        raw_path: str | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        raw_text = os.fspath(raw_path)
        anchored_parent = (
            Path(real_readlink(f"/proc/self/fd/{dir_fd}"))
            if dir_fd is not None
            else None
        )
        opens_declared_target = (
            dir_fd is None and Path(raw_text) == declared_target
        ) or (
            anchored_parent == target_parent
            and raw_text == declared_target.name
            and not flags & os.O_DIRECTORY
        )
        if opens_declared_target and not swapped:
            target_parent.rename(displaced_parent)
            target_parent.symlink_to(outside_parent, target_is_directory=True)
            swapped = True
        if dir_fd is None:
            return real_open(raw_path, flags, mode)
        return real_open(raw_path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_intermediate_directory_during_target_open)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    final_pointer_stat = os.lstat(pointer_path)
    assert swapped, "race hook must replace the intermediate target directory"
    assert target_parent.is_symlink()
    assert outside_target.read_bytes() == displaced_parent.joinpath(
        declared_target.name
    ).read_bytes()
    assert (final_pointer_stat.st_dev, final_pointer_stat.st_ino) == (
        initial_pointer_stat.st_dev,
        initial_pointer_stat.st_ino,
    )
    assert os.readlink(pointer_path) == initial_pointer_target
    assert errors, "verification must never certify an intermediate-directory race"
    assert any("generic pointer" in error for error in errors), errors


def test_routing_probe_never_exposes_raw_value_or_command_payload(
    tmp_path: Path,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    routing = manifest["components"]["test_contact"]["routing"]
    router = _container_for(manifest, "test_contact", "router")
    command = (
        "docker",
        "exec",
        router["name"],
        "python",
        "-c",
        authority._ROUTING_HASH_SCRIPT,
        routing["variable"],
    )
    raw_value = b"person@example.invalid:+55 11 99999-9999"
    fake.command_failures[command] = authority.Completed(1, raw_value, raw_value)

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)
    rendered = render_markdown(manifest)

    assert any("routing target" in error for error in errors)
    assert raw_value.decode() not in "\n".join(errors)
    assert routing["target_hash"] not in rendered
    assert routing["variable"] not in rendered
    script = authority._ROUTING_HASH_SCRIPT
    assert "os.environ" in script
    assert "print(os.environ" not in script


def test_routing_hash_script_executes_digest_without_leaking_raw_value() -> None:
    raw_value = b"person@example.invalid:+55 00 00000-0000"

    _assert_routing_hash_script(raw_value)


def test_routing_hash_script_witness_rejects_raw_value_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_value = b"person@example.invalid:+55 00 00000-0000"
    monkeypatch.setattr(
        authority,
        "_ROUTING_HASH_SCRIPT",
        "import os,sys; value=os.environ[sys.argv[1]]; print(value)",
    )

    with pytest.raises(AssertionError, match="routing hash stdout leaked the raw value"):
        _assert_routing_hash_script(raw_value)


def test_verify_manifest_converts_boundary_exceptions_to_sanitized_errors(
    tmp_path: Path,
) -> None:
    manifest = materialized_manifest(tmp_path)
    fake = FakeRuntime(manifest)
    commit = manifest["components"]["ga"]["source"]["commit"]
    endpoint_url = manifest["public_endpoints"][0]["url"]
    git_command = (
        "git",
        "-C",
        manifest["repository"]["path"],
        "rev-parse",
        f"{commit}^{{tree}}",
    )
    fake.command_exceptions[git_command] = TimeoutError(
        "RAW_ROUTING_VALUE_SHOULD_NOT_LEAK"
    )
    fake.http_exceptions[endpoint_url] = OSError("person@example.invalid")

    errors = authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert any("git tree command failed" in error for error in errors)
    assert any("public endpoint request failed" in error for error in errors)
    joined = "\n".join(errors)
    assert "RAW_ROUTING_VALUE_SHOULD_NOT_LEAK" not in joined
    assert "person@example.invalid" not in joined


def test_verify_manifest_validates_before_running_boundaries(tmp_path: Path) -> None:
    manifest = materialized_manifest(tmp_path)
    _container_for(manifest, "ga", "api")["read_only"] = False
    fake = FakeRuntime(materialized_manifest(tmp_path / "other"))

    with pytest.raises(AuthorityError, match="read_only must be true"):
        authority.verify_manifest(manifest, fake.runner, fake.http_get)

    assert fake.calls == []
    assert fake.http_calls == []


def test_cli_render_and_verify_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = materialized_manifest(tmp_path)
    manifest_path = tmp_path / "ACTIVE_RUNTIME.manifest.json"
    markdown_path = tmp_path / "ACTIVE_RUNTIME.rendered.md"
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


@pytest.mark.parametrize(
    ("existing_mode", "expected_mode"),
    ((None, 0o644), (0o640, 0o640)),
    ids=("new-output", "existing-output"),
)
def test_atomic_write_uses_default_or_preserved_output_mode(
    tmp_path: Path,
    existing_mode: int | None,
    expected_mode: int,
) -> None:
    output_path = tmp_path / "ACTIVE_RUNTIME.rendered.md"
    if existing_mode is not None:
        output_path.write_text("previous\n", encoding="utf-8")
        output_path.chmod(existing_mode)

    authority._write_text_atomic(output_path, "replacement\n")

    assert output_path.read_text(encoding="utf-8") == "replacement\n"
    assert stat.S_IMODE(output_path.stat().st_mode) == expected_mode


@pytest.mark.parametrize("existing_kind", ("symlink", "directory"))
def test_atomic_write_rejects_existing_symlink_or_non_regular_output(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    output_path = tmp_path / "ACTIVE_RUNTIME.rendered.md"
    symlink_target = tmp_path / "outside.md"
    symlink_target.write_text("outside\n", encoding="utf-8")
    if existing_kind == "symlink":
        output_path.symlink_to(symlink_target)
    else:
        output_path.mkdir()
    original_entries = set(tmp_path.iterdir())

    with pytest.raises(OSError):
        authority._write_text_atomic(output_path, "replacement\n")

    assert set(tmp_path.iterdir()) == original_entries
    if existing_kind == "symlink":
        assert output_path.is_symlink()
        assert symlink_target.read_text(encoding="utf-8") == "outside\n"
    else:
        assert output_path.is_dir()


def test_atomic_write_fsyncs_file_then_parent_directory_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "ACTIVE_RUNTIME.rendered.md"
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(descriptor: int) -> None:
        descriptor_stat = os.fstat(descriptor)
        events.append(
            "directory-fsync"
            if stat.S_ISDIR(descriptor_stat.st_mode)
            else "file-fsync"
        )
        real_fsync(descriptor)

    def recording_replace(
        source: str | os.PathLike[str],
        target: str | os.PathLike[str],
    ) -> None:
        events.append("replace")
        real_replace(source, target)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", recording_replace)

    authority._write_text_atomic(output_path, "replacement\n")

    assert events == ["file-fsync", "replace", "directory-fsync"]


def test_cli_render_replace_failure_preserves_previous_output_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = minimal_manifest()
    manifest_path = tmp_path / "ACTIVE_RUNTIME.manifest.json"
    markdown_path = tmp_path / "ACTIVE_RUNTIME.rendered.md"
    previous_bytes = b"previous verified authority\n"
    expected_bytes = render_markdown(manifest).encode("utf-8")
    _write_manifest(manifest_path, manifest)
    markdown_path.write_bytes(previous_bytes)
    original_entries = set(tmp_path.iterdir())
    real_fsync = os.fsync
    fsynced_sizes: list[int] = []
    replace_calls: list[tuple[Path, Path]] = []
    leaked_error = "person@example.invalid"

    def recording_fsync(descriptor: int) -> None:
        fsynced_sizes.append(os.fstat(descriptor).st_size)
        real_fsync(descriptor)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        target_path = Path(target)
        replace_calls.append((source_path, target_path))
        assert source_path.parent == markdown_path.parent
        assert source_path.read_bytes() == expected_bytes
        raise OSError(leaked_error)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", fail_replace)

    result = authority.main(
        ["render", "--manifest", str(manifest_path), "--output", str(markdown_path)]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert fsynced_sizes == [len(expected_bytes)]
    assert len(replace_calls) == 1
    assert replace_calls[0][1] == markdown_path
    assert markdown_path.read_bytes() == previous_bytes
    assert set(tmp_path.iterdir()) == original_entries
    assert captured.out == ""
    assert captured.err == "authority error: output could not be written\n"
    assert leaked_error not in captured.err


def test_cli_verify_failure_returns_one_and_sanitizes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = materialized_manifest(tmp_path)
    manifest_path = tmp_path / "ACTIVE_RUNTIME.manifest.json"
    _write_manifest(manifest_path, manifest)
    fake = FakeRuntime(manifest)
    endpoint = manifest["public_endpoints"][0]
    fake.http_responses[endpoint["url"]] = (503, b"person@example.invalid")
    monkeypatch.setattr(authority, "_default_runner", fake.runner)
    monkeypatch.setattr(authority, "_default_http_get", fake.http_get)

    assert authority.main(["verify", "--manifest", str(manifest_path), "--json"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["ok"] is False
    assert "person@example.invalid" not in output


def test_cli_invalid_manifest_returns_two_without_echoing_sensitive_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = minimal_manifest()
    manifest["person@example.invalid"] = "synthetic"
    manifest_path = tmp_path / "ACTIVE_RUNTIME.manifest.json"
    _write_manifest(manifest_path, manifest)

    assert authority.main(["verify", "--manifest", str(manifest_path), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "authority error" in captured.err
    assert "person@example.invalid" not in captured.err


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


def test_public_endpoint_status_code_is_closed_integer(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["public_endpoints"][0]["status_code"] = True
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="status_code"):
        load_manifest(path)


def test_container_roles_and_names_must_be_unique(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    containers = manifest["components"]["ga"]["containers"]
    containers[1]["name"] = containers[0]["name"]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="container names"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("collision", "expected_error"),
    (
        ("container_name", "container name is shared across components"),
        ("compose_project", "compose project is shared across components"),
        ("writable_mount_source", "writable mount source is shared across components"),
    ),
)
def test_manifest_rejects_cross_component_runtime_ownership_collisions(
    tmp_path: Path,
    collision: str,
    expected_error: str,
) -> None:
    manifest = minimal_manifest()
    ga = _container_for(manifest, "ga", "api")
    test_contact = _container_for(manifest, "test_contact", "api")
    if collision == "container_name":
        test_contact["name"] = ga["name"]
    elif collision == "compose_project":
        test_contact["compose_project"] = ga["compose_project"]
    elif collision == "writable_mount_source":
        shared_source = "/srv/agente-v2/shared-runtime-state"
        ga["mounts"][0] = {
            "source": shared_source,
            "destination": "/app/ga-state",
            "read_only": False,
        }
        test_contact["mounts"][0] = {
            "source": shared_source,
            "destination": "/app/test-contact-state",
            "read_only": False,
        }
    else:  # pragma: no cover - guards the test matrix itself
        raise AssertionError(collision)
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match=expected_error):
        load_manifest(path)


def test_manifest_allows_read_only_mount_source_shared_with_ops(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    ga_mount = _container_for(manifest, "ga", "api")["mounts"][0]
    ops_mount = _container_for(manifest, "ops", "web")["mounts"][0]
    ops_mount["source"] = ga_mount["source"]
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    assert load_manifest(path) == manifest


def test_file_role_must_resolve_to_declared_container(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["ga"]["files"][0]["container_role"] = "missing"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="container_role"):
        load_manifest(path)


def test_only_test_contact_may_declare_routing(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["ga"]["routing"] = dict(
        manifest["components"]["test_contact"]["routing"]
    )
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="ga routing"):
        load_manifest(path)


def test_test_contact_routing_must_identify_router(tmp_path: Path) -> None:
    manifest = minimal_manifest()
    manifest["components"]["test_contact"]["routing"]["container_role"] = "api"
    path = tmp_path / "ACTIVE_RUNTIME.json"
    _write_manifest(path, manifest)

    with pytest.raises(AuthorityError, match="router"):
        load_manifest(path)
