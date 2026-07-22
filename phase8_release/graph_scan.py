"""Fail-closed scanner for quarantined Phase 8 interfaces."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any


class ContractScanError(RuntimeError):
    """The configured scan universe could not be inspected safely."""


@dataclass(frozen=True, slots=True, order=True)
class QuarantinedOwnerFinding:
    path: str
    token: str


@dataclass(frozen=True, slots=True)
class QuarantineScanReport:
    findings: tuple[QuarantinedOwnerFinding, ...]
    active_authorities_scanned: int
    files_scanned: int


@dataclass(frozen=True, slots=True)
class RootSeparation:
    runtime_source: Path
    build_context: Path


@dataclass(frozen=True, slots=True)
class SourceSchemaBaseline:
    boundary_version: int
    boundary_tables: tuple[str, ...]
    phase5_version: int
    phase5_tables: tuple[str, ...]
    phase6_version: int
    phase6_tables: tuple[str, ...]


def _resolve_directory(value: str | Path, *, field: str) -> Path:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractScanError(f"{field} cannot be resolved") from exc
    if path.is_symlink() or not resolved.is_dir():
        raise ContractScanError(f"{field} must be a non-symlink directory")
    return resolved


def assert_runtime_source_and_build_context_disjoint(
    runtime_source: str | Path,
    build_context: str | Path,
) -> RootSeparation:
    """Reject identical, nested, or aliased runtime/build roots."""

    runtime = _resolve_directory(runtime_source, field="runtime_source")
    context = _resolve_directory(build_context, field="build_context")
    if (
        runtime == context
        or runtime.is_relative_to(context)
        or context.is_relative_to(runtime)
    ):
        raise ContractScanError("runtime source and build context must be disjoint")
    return RootSeparation(runtime_source=runtime, build_context=context)


def _closed_schema_identity(
    version: object,
    tables: object,
    *,
    owner: str,
) -> tuple[int, tuple[str, ...]]:
    if type(version) is not int or version < 1:
        raise ContractScanError(f"{owner} schema version is invalid")
    try:
        names = tuple(table.name if hasattr(table, "name") else table for table in tables)
    except TypeError as exc:
        raise ContractScanError(f"{owner} table universe is not iterable") from exc
    if (
        not names
        or any(not isinstance(name, str) or not name for name in names)
        or len(set(names)) != len(names)
    ):
        raise ContractScanError(f"{owner} table universe is invalid")
    return version, names


def inspect_source_schema_baseline() -> SourceSchemaBaseline:
    """Read the package-owned pre-Phase-8 schema identities without opening a DB."""

    from reservation_boundary import schema as boundary_schema
    from reservation_execution import schema as phase5_schema
    from reservation_followup import schema as phase6_schema

    boundary_version, boundary_tables = _closed_schema_identity(
        boundary_schema.SCHEMA_VERSION,
        boundary_schema.TABLE_NAMES,
        owner="boundary",
    )
    phase5_version, phase5_tables = _closed_schema_identity(
        phase5_schema.SCHEMA_VERSION,
        phase5_schema.schema_contract(),
        owner="phase5",
    )
    phase6_version, phase6_tables = _closed_schema_identity(
        phase6_schema.SCHEMA_VERSION,
        phase6_schema.schema_contract(),
        owner="phase6",
    )
    return SourceSchemaBaseline(
        boundary_version=boundary_version,
        boundary_tables=boundary_tables,
        phase5_version=phase5_version,
        phase5_tables=phase5_tables,
        phase6_version=phase6_version,
        phase6_tables=phase6_tables,
    )


def _string_tuple(value: Any, *, field: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or (not allow_empty and not item)
        for item in value
    ):
        raise ContractScanError(f"{field} must be a JSON array of strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise ContractScanError(f"{field} contains duplicates")
    return result


def _relative_path(value: str, *, field: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise ContractScanError(f"{field} contains an unsafe relative path: {value!r}")
    return path


def _safe_path(root: Path, relative: str, *, field: str) -> Path:
    logical = _relative_path(relative, field=field)
    path = root.joinpath(*logical.parts)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractScanError(f"cannot resolve {field} path {relative!r}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContractScanError(f"{field} escapes repository root: {relative!r}") from exc
    if path.is_symlink() or not resolved.is_file():
        raise ContractScanError(f"{field} is not a regular non-symlink file: {relative!r}")
    return resolved


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ContractScanError(f"cannot read covered UTF-8 file: {path}") from exc


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractScanError(f"cannot load quarantine manifest: {path}") from exc
    if not isinstance(value, dict):
        raise ContractScanError("quarantine manifest must be a JSON object")
    return value


def scan_for_quarantined_owners(
    repository_root: str | Path,
    quarantine_manifest: str | Path,
) -> QuarantineScanReport:
    """Scan the manifest's closed active universe for forbidden interfaces.

    Active authorities are always inspected before global exclusions are applied.
    Any missing path, symlink, malformed manifest, I/O error, or UTF-8 failure blocks
    the scan instead of shrinking its universe.
    """

    try:
        root = Path(repository_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContractScanError("repository root cannot be resolved") from exc
    if not root.is_dir():
        raise ContractScanError("repository root must be a directory")

    manifest = _load_manifest(Path(quarantine_manifest).resolve(strict=True))
    active_paths = _string_tuple(
        manifest.get("active_authority_paths"), field="active_authority_paths"
    )
    extensions = frozenset(
        _string_tuple(
            manifest.get("active_scan_extensions"),
            field="active_scan_extensions",
            allow_empty=True,
        )
    )
    scan_roots = _string_tuple(manifest.get("active_scan_roots"), field="active_scan_roots")
    forbidden = _string_tuple(
        manifest.get("forbidden_active_tokens"), field="forbidden_active_tokens"
    )

    exclusions_value = manifest.get("scan_exclusions")
    if not isinstance(exclusions_value, list):
        raise ContractScanError("scan_exclusions must be a JSON array")
    exclusions: set[str] = set()
    for index, item in enumerate(exclusions_value):
        if not isinstance(item, dict) or set(item) != {"path", "reason"}:
            raise ContractScanError(f"scan_exclusions[{index}] has an invalid shape")
        path = item["path"]
        reason = item["reason"]
        if not isinstance(path, str) or not path or not isinstance(reason, str) or not reason:
            raise ContractScanError(f"scan_exclusions[{index}] must contain strings")
        _relative_path(path, field=f"scan_exclusions[{index}].path")
        if path in exclusions:
            raise ContractScanError(f"duplicate scan exclusion: {path}")
        exclusions.add(path)

    findings: set[QuarantinedOwnerFinding] = set()
    scanned: set[str] = set()

    def inspect(relative: str, path: Path) -> None:
        text = _read_text(path)
        scanned.add(relative)
        for token in forbidden:
            if token in text:
                findings.add(QuarantinedOwnerFinding(relative, token))

    for relative in active_paths:
        inspect(relative, _safe_path(root, relative, field="active_authority_paths"))

    for relative_root in scan_roots:
        logical_root = _relative_path(relative_root, field="active_scan_roots")
        scan_root = root.joinpath(*logical_root.parts)
        try:
            resolved_root = scan_root.resolve(strict=True)
            resolved_root.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ContractScanError(f"invalid active scan root: {relative_root!r}") from exc
        if scan_root.is_symlink() or not resolved_root.is_dir():
            raise ContractScanError(f"active scan root is not a regular directory: {relative_root!r}")

        try:
            paths = sorted(resolved_root.rglob("*"))
        except OSError as exc:
            raise ContractScanError(f"cannot enumerate active scan root: {relative_root!r}") from exc
        for path in paths:
            if path.is_symlink():
                raise ContractScanError(f"symlink in active scan universe: {path}")
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            if relative in exclusions or path.suffix.lower() not in extensions:
                continue
            inspect(relative, path)

    return QuarantineScanReport(
        findings=tuple(sorted(findings)),
        active_authorities_scanned=len(active_paths),
        files_scanned=len(scanned),
    )


__all__ = (
    "ContractScanError",
    "QuarantineScanReport",
    "QuarantinedOwnerFinding",
    "RootSeparation",
    "SourceSchemaBaseline",
    "assert_runtime_source_and_build_context_disjoint",
    "inspect_source_schema_baseline",
    "scan_for_quarantined_owners",
)
