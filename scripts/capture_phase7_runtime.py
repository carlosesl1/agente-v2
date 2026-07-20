#!/usr/bin/env python3
"""Capture a sanitized isolated clone of the dirty Chapada runtime."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Final, Iterable


EXPECTED_RUNTIME_HEAD: Final = "57408d8b2040399bc25ee7957505208079458884"
DEFAULT_UNTRACKED_ALLOWLIST: Final = (
    "docs/architecture/maya-current-runtime-map.html",
    "docs/architecture/maya-current-runtime-map.md",
    "domain/agent_tool_feedback.py",
    "qa/model_benchmark/README.md",
    "qa/model_benchmark/__init__.py",
    "qa/model_benchmark/matrix.yaml",
    "qa/model_benchmark/review.py",
    "qa/model_benchmark/runner.py",
    "qa/model_benchmark/scoring.py",
    "tests/test_agent_tool_feedback.py",
    "tests/test_manychat_single_confirmation_flow.py",
    "tests/test_model_benchmark.py",
)
PII_REDACTION_ALLOWLIST: Final = frozenset(
    (
        "tests/test_app_llm_central_webhook.py",
        "tests/test_app_shadow_webhook.py",
        "tests/test_manychat_single_confirmation_flow.py",
    )
)
_SAFE_TEXT_SUFFIXES: Final = frozenset(
    (".bash", ".html", ".json", ".lock", ".md", ".py", ".sh", ".toml", ".yaml", ".yml")
)
_SECRET_PATTERNS: Final = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|private[_-]?key|secret)\b"
        r"\s*[:=]\s*['\"](?P<secret_value>[^'\"\s]{16,})['\"]"
    ),
)
_PHONE_RE: Final = re.compile(r"(?<!\d)\+\d{10,15}(?!\d)")
_CPF_RE: Final = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_LONG_DIGITS_RE: Final = re.compile(r"(?<!\d)\d{9,16}(?!\d)")
_ALLOWED_SECRET_VALUES: Final = (
    "placeholder",
    "example",
    "dummy",
    "test",
    "redacted",
    "changeme",
    "none",
)


class CaptureRejected(RuntimeError):
    """Source cannot be captured without violating the closed safety contract."""


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    head: str
    tree: str
    status_hash: str
    tracked_diff_hash: str
    status_entries: int
    untracked: tuple[tuple[str, str, int, str], ...]


@dataclass(frozen=True, slots=True)
class CaptureResult:
    source_head: str
    source_tree: str
    synthetic_baseline_commit: str
    synthetic_baseline_tree: str
    included_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    source_unchanged: bool


def _run(
    args: list[str],
    *,
    cwd: Path,
    text: bool = True,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> str | bytes:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=text,
        input=input_bytes,
        capture_output=True,
        env=env,
    )
    return completed.stdout


def _git_text(path: Path, *args: str) -> str:
    return str(_run(["git", *args], cwd=path)).strip()


def _git_bytes(path: Path, *args: str) -> bytes:
    return bytes(_run(["git", *args], cwd=path, text=False))


def _safe_relative(raw: str) -> str:
    if type(raw) is not str or not raw:
        raise CaptureRejected("git path must be exact nonempty text")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise CaptureRejected("git path escapes the runtime root")
    return path.as_posix()


def _forbidden_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    lowered = tuple(part.casefold() for part in path.parts)
    name = lowered[-1]
    if any(part.startswith(".env") for part in lowered):
        return True
    if any("backup" in part for part in lowered):
        return True
    if lowered[:3] == ("qa", "maya_test_lab", "scenarios"):
        return True
    if name.endswith((".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key")):
        return True
    if name in ("id_rsa", "id_ed25519"):
        return True
    return False


def _file_digest(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def source_fingerprint(source: Path) -> SourceFingerprint:
    source = source.resolve()
    if not (source / ".git").exists():
        raise CaptureRejected("source is not a Git working tree")
    status = _git_bytes(source, "status", "--porcelain=v1", "-z", "-uall")
    diff = _git_bytes(source, "diff", "--binary", "--full-index", "HEAD", "--")
    raw_untracked = _git_bytes(source, "ls-files", "--others", "--exclude-standard", "-z")
    rows = []
    for raw in sorted(item.decode("utf-8") for item in raw_untracked.split(b"\0") if item):
        relative = _safe_relative(raw)
        path = source / relative
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            rows.append((relative, hashlib.sha256(target.encode()).hexdigest(), len(target), "symlink"))
        elif stat.S_ISREG(metadata.st_mode):
            digest, size = _file_digest(path)
            rows.append((relative, digest, size, "file"))
        else:
            rows.append((relative, "0" * 64, 0, "other"))
    return SourceFingerprint(
        _git_text(source, "rev-parse", "HEAD"),
        _git_text(source, "rev-parse", "HEAD^{tree}"),
        hashlib.sha256(status).hexdigest(),
        hashlib.sha256(diff).hexdigest(),
        len([item for item in status.split(b"\0") if item]),
        tuple(rows),
    )


def _synthetic_digits(value: str) -> str:
    digest = hashlib.sha256(value.encode()).digest()
    generated = "9" + "".join(str(byte % 10) for byte in digest)
    return generated[: len(value)]


def _sanitize_allowlisted_test(text: str) -> tuple[str, int]:
    count = 0

    def replace_digits(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return _synthetic_digits(match.group(0))

    text = _LONG_DIGITS_RE.sub(replace_digits, text)
    text, phone_count = _PHONE_RE.subn("+5500000000000", text)
    count += phone_count
    text, cpf_count = _CPF_RE.subn("000.000.000-00", text)
    count += cpf_count
    for key, replacement in (
        ("first_name", "Synthetic"),
        ("last_name", "Lead"),
        ("name", "Synthetic Lead"),
    ):
        pattern = re.compile(
            rf"(?P<prefix>['\"]{key}['\"]\s*:\s*['\"])[^'\"]*(?P<suffix>['\"])"
        )
        text, replacements = pattern.subn(
            lambda match, value=replacement: (
                match.group("prefix") + value + match.group("suffix")
            ),
            text,
        )
        count += replacements
    return text, count


def _scan_text(
    path: Path,
    relative: str,
    *,
    allow_pii_redaction: bool = False,
) -> tuple[bytes, int]:
    if type(allow_pii_redaction) is not bool:
        raise TypeError("allow_pii_redaction must be an exact bool")
    if path.is_symlink() or not path.is_file():
        raise CaptureRejected(f"non-regular capture input: {relative}")
    if _forbidden_path(relative):
        raise CaptureRejected(f"forbidden capture path: {relative}")
    suffix = path.suffix.casefold()
    if suffix not in _SAFE_TEXT_SUFFIXES and path.name not in (
        ".dockerignore",
        ".gitignore",
        "Dockerfile",
    ):
        raise CaptureRejected(f"non-text capture path: {relative}")
    data = path.read_bytes()
    if len(data) > 2_000_000 or b"\0" in data:
        raise CaptureRejected(f"binary/oversized capture input: {relative}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaptureRejected(f"non-UTF8 capture input: {relative}") from exc
    redactions = 0
    if allow_pii_redaction:
        text, redactions = _sanitize_allowlisted_test(text)
    for match in _PHONE_RE.finditer(text):
        if (
            allow_pii_redaction
            and match.group(0) == "+5500000000000"
        ):
            continue
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        context = text[line_start:line_end].casefold()
        if not (
            "contact_phone" in context
            and "e.164" in context
            and "exemplo" in context
        ):
            raise CaptureRejected(f"PII-like literal in capture input: {relative}")
    for match in _CPF_RE.finditer(text):
        if (
            allow_pii_redaction
            and match.group(0) == "000.000.000-00"
        ):
            continue
        raise CaptureRejected(f"PII-like literal in capture input: {relative}")
    for pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            value = (
                match.group("secret_value").casefold()
                if "secret_value" in pattern.groupindex
                else match.group(0).casefold()
            )
            if not any(token in value for token in _ALLOWED_SECRET_VALUES) and "${" not in value:
                raise CaptureRejected(f"secret-like literal in capture input: {relative}")
    return text.encode("utf-8"), redactions


def _changed_paths(source: Path) -> tuple[str, ...]:
    raw = _git_bytes(source, "diff", "--name-only", "-z", "HEAD", "--")
    return tuple(sorted(_safe_relative(item.decode("utf-8")) for item in raw.split(b"\0") if item))


def _untracked_paths(source: Path) -> tuple[str, ...]:
    raw = _git_bytes(source, "ls-files", "--others", "--exclude-standard", "-z")
    return tuple(sorted(_safe_relative(item.decode("utf-8")) for item in raw.split(b"\0") if item))


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temp = Path(handle.name)
    temp.replace(path)


def _assignment(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    raise CaptureRejected(f"missing runtime contract assignment: {name}")


def _literal_node(
    tree: ast.Module,
    node: ast.AST,
    *,
    stack: tuple[str, ...],
) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in stack:
            raise CaptureRejected("cyclic runtime contract constant")
        return _literal_node(
            tree,
            _assignment(tree, node.id),
            stack=(*stack, node.id),
        )
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values: list[object] = []
        for element in node.elts:
            if isinstance(element, ast.Starred):
                expanded = _literal_node(tree, element.value, stack=stack)
                if type(expanded) not in (tuple, list, set, frozenset):
                    raise CaptureRejected("starred runtime constant is not a closed collection")
                values.extend(expanded)
            else:
                values.append(_literal_node(tree, element, stack=stack))
        if isinstance(node, ast.Tuple):
            return tuple(values)
        if isinstance(node, ast.List):
            return values
        return set(values)
    if isinstance(node, ast.Dict):
        if any(key is None for key in node.keys):
            raise CaptureRejected("runtime contract dictionary unpacking is forbidden")
        return {
            _literal_node(tree, key, stack=stack): _literal_node(tree, value, stack=stack)
            for key, value in zip(node.keys, node.values, strict=True)
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _literal_node(tree, node.operand, stack=stack)
        if type(operand) not in (int, float):
            raise CaptureRejected("runtime unary literal is not numeric")
        return operand if isinstance(node.op, ast.UAdd) else -operand
    raise CaptureRejected(
        f"runtime contract contains nonliteral AST: {type(node).__name__}"
    )


def _literal(tree: ast.Module, name: str) -> object:
    return _literal_node(tree, _assignment(tree, name), stack=(name,))


def _function_signatures(root: Path) -> list[dict[str, object]]:
    targets = {
        "_process_event",
        "execute_hermes_native_tool",
        "native_tool_schemas",
        "plan",
    }
    files = (
        root / "app.py",
        root / "domain/chapada_native_tools.py",
        root / "domain/hermes_native_runner.py",
        root / "domain/tool_executor.py",
        root / ".hermes/plugins/chapada_leads_tools/__init__.py",
    )
    rows = []
    for path in files:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in targets:
                positional = tuple(arg.arg for arg in (*node.args.posonlyargs, *node.args.args))
                rows.append(
                    {
                        "async": isinstance(node, ast.AsyncFunctionDef),
                        "file": path.relative_to(root).as_posix(),
                        "kwonly": [arg.arg for arg in node.args.kwonlyargs],
                        "name": node.name,
                        "positional": list(positional),
                        "vararg": node.args.vararg.arg if node.args.vararg else None,
                    }
                )
    return sorted(rows, key=lambda row: (str(row["file"]), str(row["name"]), tuple(row["positional"])))


_SCHEMA_COPY_KEYS: Final = frozenset(
    ("$comment", "default", "description", "example", "examples", "title")
)


def _schema_shape(value: object) -> object:
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise CaptureRejected("runtime JSON schema keys must be exact strings")
        return {
            key: _schema_shape(item)
            for key, item in sorted(value.items())
            if key not in _SCHEMA_COPY_KEYS
        }
    if type(value) in (list, tuple):
        return [_schema_shape(item) for item in value]
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise CaptureRejected("runtime JSON schema contains a non-JSON shape value")


def build_runtime_contract_manifest(root: Path) -> dict[str, object]:
    root = root.resolve()
    source = root / "domain/chapada_native_tools.py"
    if not source.is_file():
        raise CaptureRejected("runtime native tool source is missing")
    tree = ast.parse(source.read_text())
    executable = _literal(tree, "V2_ONLY_NATIVE_EXECUTABLE_TOOL_NAMES")
    commit = _literal(tree, "CHAPADA_COMMIT_STATE_TOOL")
    readonly = _literal(tree, "MAYA_VISIBLE_READONLY_NATIVE_TOOL_NAMES")
    writes = _literal(tree, "MAYA_VISIBLE_WRITE_TOOL_NAMES")
    schemas = _literal(tree, "_NATIVE_TOOL_SCHEMAS")
    if not all(type(item) is tuple for item in (executable, readonly, writes, schemas)) or type(commit) is not str:
        raise CaptureRejected("runtime tool constants have invalid exact types")
    active = tuple(executable) + (commit,)
    if len(set(active)) != len(active) or set(active) != set(readonly) | set(writes) | {commit}:
        raise CaptureRejected("runtime active/read/write/commit catalog is inconsistent")
    schema_by_name = {}
    for item in schemas:
        if type(item) is not dict or set(item) < {"name", "parameters"}:
            raise CaptureRejected("runtime tool schema shape is invalid")
        name = item["name"]
        parameters = item["parameters"]
        if type(name) is not str or type(parameters) is not dict or name in schema_by_name:
            raise CaptureRejected("runtime tool schema identity is invalid")
        schema_by_name[name] = parameters
    if not set(active) <= set(schema_by_name):
        raise CaptureRejected("runtime active tool is missing a JSON schema")
    tools = []
    for name in sorted(active):
        category = "state_commit" if name == commit else "read" if name in readonly else "write"
        parameters = _schema_shape(schema_by_name[name])
        material = json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if any(pattern.search(material) for pattern in _SECRET_PATTERNS) or _PHONE_RE.search(material):
            raise CaptureRejected("runtime JSON schema contains secret/PII-like defaults")
        tools.append(
            {
                "category": category,
                "name": name,
                "parameters": parameters,
                "schema_hash": hashlib.sha256(material.encode()).hexdigest(),
            }
        )
    source_paths = (
        "app.py",
        "domain/chapada_native_tools.py",
        "domain/hermes_native_runner.py",
        "domain/tool_executor.py",
        ".hermes/plugins/chapada_leads_tools/__init__.py",
    )
    source_hashes = {}
    for relative in source_paths:
        path = root / relative
        if path.is_file():
            source_hashes[relative] = _file_digest(path)[0]
    return {
        "counts": {
            "active": len(active),
            "read": len(readonly),
            "state_commit": 1,
            "write": len(writes),
        },
        "function_signatures": _function_signatures(root),
        "schema_version": 1,
        "source_hashes": source_hashes,
        "tools": tools,
    }


def capture_runtime(
    *,
    source: Path,
    output: Path,
    manifest_path: Path,
    contract_manifest_path: Path,
    expected_head: str = EXPECTED_RUNTIME_HEAD,
    untracked_allowlist: tuple[str, ...] = DEFAULT_UNTRACKED_ALLOWLIST,
) -> CaptureResult:
    source = source.resolve()
    output = output.resolve()
    manifest_path = manifest_path.resolve()
    contract_manifest_path = contract_manifest_path.resolve()
    if output.exists():
        raise CaptureRejected("output already exists; it is never removed or reused")
    if not output.parent.is_dir():
        raise CaptureRejected("output parent must already exist")
    if output == source or source in output.parents or output in source.parents:
        raise CaptureRejected("source/output roots must be independent")
    before = source_fingerprint(source)
    if type(expected_head) is not str or before.head != expected_head:
        raise CaptureRejected("runtime HEAD does not match the authenticated base")
    allowlist = tuple(sorted(_safe_relative(item) for item in untracked_allowlist))
    if len(set(allowlist)) != len(allowlist):
        raise CaptureRejected("untracked allowlist contains duplicates")
    untracked = _untracked_paths(source)
    if untracked != allowlist:
        raise CaptureRejected("untracked runtime inventory differs from the exact allowlist")
    changed = _changed_paths(source)
    excluded = tuple(sorted(path for path in changed if _forbidden_path(path)))
    included = tuple(sorted(path for path in changed if path not in excluded))
    transforms: dict[str, tuple[bytes, int]] = {}
    for relative in included:
        path = source / relative
        if path.exists():
            payload, count = _scan_text(
                path,
                relative,
                allow_pii_redaction=relative.startswith("tests/"),
            )
            if payload != path.read_bytes():
                transforms[relative] = (payload, count)
    for relative in untracked:
        path = source / relative
        payload, count = _scan_text(
            path,
            relative,
            allow_pii_redaction=relative in PII_REDACTION_ALLOWLIST,
        )
        if payload != path.read_bytes():
            transforms[relative] = (payload, count)

    patch = (
        _git_bytes(source, "diff", "--binary", "--full-index", "HEAD", "--", *included)
        if included
        else b""
    )
    contract_payload: dict[str, object]
    with tempfile.TemporaryDirectory(prefix="phase7-runtime-stage-", dir=output.parent) as stage_name:
        stage = Path(stage_name)
        clone = stage / "replica"
        _run(["git", "clone", "--no-local", "--quiet", str(source), str(clone)], cwd=stage)
        tracked_in_clone = _git_bytes(clone, "ls-files", "-z")
        for raw in tracked_in_clone.split(b"\0"):
            if not raw:
                continue
            relative = _safe_relative(raw.decode("utf-8"))
            if _forbidden_path(relative):
                target = clone / relative
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
        if patch:
            patch_path = stage / "tracked.patch"
            patch_path.write_bytes(patch)
            _run(["git", "apply", "--binary", "--whitespace=nowarn", str(patch_path)], cwd=clone)
        for relative in untracked:
            source_file = source / relative
            target = clone / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, target, follow_symlinks=False)
        for relative, (payload, _) in transforms.items():
            target = clone / relative
            if not target.is_file():
                raise CaptureRejected(f"redaction target is not a regular file: {relative}")
            target.write_bytes(payload)
        for relative in included:
            source_file = source / relative
            clone_file = clone / relative
            if source_file.exists():
                expected = transforms.get(relative, (source_file.read_bytes(), 0))[0]
                if not clone_file.is_file() or clone_file.read_bytes() != expected:
                    raise CaptureRejected(f"reconstructed tracked file differs: {relative}")
            elif clone_file.exists():
                raise CaptureRejected(f"reconstructed deletion differs: {relative}")
        for relative in untracked:
            expected = transforms.get(relative, ((source / relative).read_bytes(), 0))[0]
            if (clone / relative).read_bytes() != expected:
                raise CaptureRejected(f"reconstructed untracked file differs: {relative}")
        _git_text(clone, "config", "user.email", "phase7-capture@example.invalid")
        _git_text(clone, "config", "user.name", "Phase 7 Capture")
        _git_text(clone, "add", "-A")
        source_date = _git_text(source, "show", "-s", "--format=%aI", "HEAD")
        commit_env = dict(os.environ)
        commit_env.update(
            {
                "GIT_AUTHOR_DATE": source_date,
                "GIT_COMMITTER_DATE": source_date,
                "TZ": "UTC",
            }
        )
        _run(
            ["git", "commit", "--quiet", "-m", "phase7 synthetic runtime baseline"],
            cwd=clone,
            env=commit_env,
        )
        if _git_text(clone, "status", "--porcelain"):
            raise CaptureRejected("synthetic baseline did not close cleanly")
        contract_payload = build_runtime_contract_manifest(clone)
        baseline_commit = _git_text(clone, "rev-parse", "HEAD")
        baseline_tree = _git_text(clone, "rev-parse", "HEAD^{tree}")
        clone.rename(output)

    after = source_fingerprint(source)
    if after != before:
        raise CaptureRejected("source runtime changed during capture")
    contract_material = json.dumps(
        contract_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    _atomic_json(contract_manifest_path, contract_payload)
    manifest_payload: dict[str, object] = {
        "excluded_paths": list(excluded),
        "included_paths": [
            {
                "path": relative,
                "sha256": _file_digest(source / relative)[0] if (source / relative).is_file() else None,
                "status": "present" if (source / relative).is_file() else "deleted",
            }
            for relative in included
        ],
        "live_capabilities_executed": [],
        "local_operations": ["git_clone", "git_apply", "file_copy", "local_commit"],
        "redacted_paths": [
            {
                "path": relative,
                "redaction_count": transforms[relative][1],
                "replica_sha256": hashlib.sha256(transforms[relative][0]).hexdigest(),
                "source_sha256": _file_digest(source / relative)[0],
            }
            for relative in sorted(transforms)
        ],
        "runtime_contract_manifest_sha256": hashlib.sha256(contract_material.encode()).hexdigest(),
        "schema_version": 1,
        "source_head": before.head,
        "source_status_entries": before.status_entries,
        "source_status_hash": before.status_hash,
        "source_tracked_diff_hash": before.tracked_diff_hash,
        "source_tree": before.tree,
        "source_unchanged": True,
        "synthetic_baseline_commit": baseline_commit,
        "synthetic_baseline_tree": baseline_tree,
        "untracked_paths": [
            {"kind": kind, "path": path, "sha256": digest, "size": size}
            for path, digest, size, kind in before.untracked
        ],
    }
    _atomic_json(manifest_path, manifest_payload)
    return CaptureResult(
        before.head,
        before.tree,
        baseline_commit,
        baseline_tree,
        included,
        excluded,
        untracked,
        True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--contract-manifest", required=True, type=Path)
    parser.add_argument("--expected-head", default=EXPECTED_RUNTIME_HEAD)
    args = parser.parse_args()
    result = capture_runtime(
        source=args.source,
        output=args.output,
        manifest_path=args.manifest,
        contract_manifest_path=args.contract_manifest,
        expected_head=args.expected_head,
    )
    print(
        json.dumps(
            {
                "excluded": len(result.excluded_paths),
                "included": len(result.included_paths),
                "output": str(args.output.resolve()),
                "source_unchanged": result.source_unchanged,
                "untracked": len(result.untracked_paths),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
