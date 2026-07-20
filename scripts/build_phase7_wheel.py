#!/usr/bin/env python3
"""Build a byte-reproducible pure-Python wheel using only the stdlib."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import stat
import tempfile
import tomllib
from types import MappingProxyType
from typing import Final
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT: Final = Path(__file__).resolve().parents[1]
ZIP_TIME: Final = (1980, 1, 1, 0, 0, 0)
DIST_NAME: Final = "chapada_reservation_kernel"
DIST_INFO: Final = f"{DIST_NAME}-0.7.0.dist-info"
WHEEL_NAME: Final = f"{DIST_NAME}-0.7.0-py3-none-any.whl"
METADATA: Final = (
    "Metadata-Version: 2.3\n"
    "Name: chapada-reservation-kernel\n"
    "Version: 0.7.0\n"
    "Requires-Python: >=3.12\n"
    "\n"
).encode("utf-8")
WHEEL: Final = (
    "Wheel-Version: 1.0\n"
    "Generator: phase7-stdlib-wheel\n"
    "Root-Is-Purelib: true\n"
    "Tag: py3-none-any\n"
    "\n"
).encode("utf-8")


class WheelBuildError(ValueError):
    """Raised when the closed wheel input contract is violated."""


def _project() -> tuple[str, tuple[str, ...]]:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project")
    tool = payload.get("tool", {}).get("phase7-wheel")
    if type(project) is not dict or type(tool) is not dict:
        raise WheelBuildError("project and tool.phase7-wheel tables are required")
    expected = {
        "name": "chapada-reservation-kernel",
        "version": "0.7.0",
        "requires-python": ">=3.12",
        "dependencies": [],
    }
    if project != expected:
        raise WheelBuildError("project metadata differs from the closed Phase 7 contract")
    raw_packages = tool.get("packages")
    if type(raw_packages) is not list or not raw_packages:
        raise WheelBuildError("tool.phase7-wheel.packages must be a non-empty list")
    if any(type(item) is not str or not item.isidentifier() for item in raw_packages):
        raise WheelBuildError("package names must be exact Python identifiers")
    packages = tuple(raw_packages)
    if len(packages) != len(set(packages)):
        raise WheelBuildError("duplicate package name")
    return project["version"], packages


def _normalized_python(path: Path) -> bytes:
    if path.is_symlink():
        raise WheelBuildError(f"symlink is forbidden: {path.relative_to(ROOT)}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WheelBuildError(f"non-UTF-8 Python source: {path.relative_to(ROOT)}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _package_payloads(packages: tuple[str, ...]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for package_name in packages:
        package = ROOT / package_name
        if package.is_symlink() or not package.is_dir():
            raise WheelBuildError(f"package directory is absent or unsafe: {package_name}")
        sources = tuple(sorted(package.rglob("*.py")))
        if not sources or package / "__init__.py" not in sources:
            raise WheelBuildError(f"package has no __init__.py: {package_name}")
        for source in sources:
            if source.is_symlink() or not source.is_file():
                raise WheelBuildError(f"unsafe package source: {source.relative_to(ROOT)}")
            relative = source.relative_to(ROOT).as_posix()
            payloads[relative] = _normalized_python(source)
    return payloads


def _urlsafe_sha256(payload: bytes) -> str:
    digest = hashlib.sha256(payload).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _record(payloads: dict[str, bytes], record_name: str) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name in sorted((*payloads, record_name)):
        if name == record_name:
            writer.writerow((name, "", ""))
        else:
            payload = payloads[name]
            writer.writerow((name, f"sha256={_urlsafe_sha256(payload)}", len(payload)))
    return stream.getvalue().encode("utf-8")


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=ZIP_TIME)
    info.compress_type = ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build_wheel(output_dir: Path) -> Path:
    version, packages = _project()
    if version != "0.7.0":
        raise WheelBuildError("unexpected project version")
    payloads = _package_payloads(packages)
    payloads.update(
        {
            f"{DIST_INFO}/METADATA": METADATA,
            f"{DIST_INFO}/WHEEL": WHEEL,
            f"{DIST_INFO}/top_level.txt": ("\n".join(packages) + "\n").encode("utf-8"),
        }
    )
    record_name = f"{DIST_INFO}/RECORD"
    payloads[record_name] = _record(payloads, record_name)
    ordered = MappingProxyType(dict(sorted(payloads.items())))
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / WHEEL_NAME
    with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".phase7-wheel-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name, payload in ordered.items():
                archive.writestr(_zip_info(name), payload, compress_type=ZIP_DEFLATED, compresslevel=9)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    wheel = build_wheel(args.output_dir)
    print(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
