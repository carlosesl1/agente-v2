"""Deterministic stdlib wheel contract for Phase 7."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
WHEEL_NAME = "chapada_reservation_kernel-0.7.0-py3-none-any.whl"
DIST_INFO = "chapada_reservation_kernel-0.7.0.dist-info"
PACKAGES = (
    "reservation_domain",
    "reservation_lookup",
    "reservation_confirmation",
    "reservation_execution",
    "reservation_followup",
    "reservation_boundary",
)


def _build(output: Path) -> Path:
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "build_phase7_wheel.py"),
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"wheel build failed: exit={completed.returncode} stderr={completed.stderr!r}"
        )
    wheel = output / WHEEL_NAME
    if not wheel.is_file():
        raise AssertionError(f"wheel was not created: {wheel}")
    return wheel


def _allowed_wheel_path(name: str) -> bool:
    if name.startswith(f"{DIST_INFO}/"):
        return name in {
            f"{DIST_INFO}/METADATA",
            f"{DIST_INFO}/WHEEL",
            f"{DIST_INFO}/top_level.txt",
            f"{DIST_INFO}/RECORD",
        }
    first, separator, remainder = name.partition("/")
    return bool(separator and first in PACKAGES and remainder.endswith(".py"))


def _urlsafe_sha256(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii")


class Phase7PackageTests(unittest.TestCase):
    def test_project_metadata_declares_closed_distribution(self) -> None:
        payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            payload["project"],
            {
                "name": "chapada-reservation-kernel",
                "version": "0.7.0",
                "requires-python": ">=3.12",
                "dependencies": [],
            },
        )
        self.assertEqual(tuple(payload["tool"]["phase7-wheel"]["packages"]), PACKAGES)
        self.assertNotIn("build-system", payload)

    def test_two_builds_are_byte_identical_closed_and_self_hashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase7-wheel-test-") as raw:
            base = Path(raw)
            first = _build(base / "first")
            second = _build(base / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                names = archive.namelist()
                self.assertEqual(names, sorted(names))
                self.assertTrue(all(_allowed_wheel_path(name) for name in names))
                self.assertIn(f"{DIST_INFO}/RECORD", names)
                for info in archive.infolist():
                    self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                    self.assertEqual(info.external_attr >> 16, 0o100644)
                rows = list(
                    csv.reader(
                        io.StringIO(
                            archive.read(f"{DIST_INFO}/RECORD").decode("utf-8")
                        )
                    )
                )
                self.assertEqual([row[0] for row in rows], names)
                for name, digest, size in rows:
                    if name == f"{DIST_INFO}/RECORD":
                        self.assertEqual((digest, size), ("", ""))
                        continue
                    payload = archive.read(name)
                    self.assertEqual(digest, f"sha256={_urlsafe_sha256(payload)}")
                    self.assertEqual(size, str(len(payload)))

    def test_installed_wheel_imports_without_checkout_on_sys_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase7-wheel-install-") as raw:
            base = Path(raw)
            wheel = _build(base / "wheel")
            target = base / "target"
            install = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--target",
                    str(target),
                    str(wheel),
                ],
                cwd=base,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            smoke = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import json,sys; "
                        f"sys.path.insert(0, {str(target)!r}); "
                        "import reservation_boundary as package; "
                        "print(json.dumps({'version': package.__version__, "
                        "'file': package.__file__}, sort_keys=True))"
                    ),
                ],
                cwd=base,
                env={"PATH": os.environ.get("PATH", "")},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(smoke.returncode, 0, smoke.stderr)
            payload = json.loads(smoke.stdout)
            self.assertEqual(payload["version"], "0.7.0")
            self.assertTrue(Path(payload["file"]).resolve().is_relative_to(target.resolve()))
            self.assertNotIn(str(ROOT), smoke.stdout)


if __name__ == "__main__":
    unittest.main()
