"""Re-execute frozen Phase 7 contracts; never certify V2 from historical evidence.

The Phase 8 entry pins this Phase 7 closeout. Six version/manifest tests are
historical: their unmodified assertions must run on their original source.
All other kernel/behavior tests still exercise the current checkout.
"""
from __future__ import annotations

import atexit
from functools import lru_cache
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]
PHASE7_COMMIT = "93682024b4867d3e313324339a7060d5351dcd3d"
PHASE7_TREE = "b779e35c671f3050d056c6ef3c8c0700f5b13f35"


@lru_cache(maxsize=1)
def _snapshot() -> tuple[Path, Path]:
    tree = subprocess.check_output(
        ["git", "rev-parse", f"{PHASE7_COMMIT}^{{tree}}"], cwd=ROOT, text=True,
    ).strip()
    if tree != PHASE7_TREE:
        raise AssertionError("historical Phase 7 tree identity mismatch")
    archive = subprocess.check_output(["git", "archive", PHASE7_COMMIT], cwd=ROOT)
    temporary = tempfile.TemporaryDirectory(prefix="v2-phase7-history-")
    atexit.register(temporary.cleanup)
    base = Path(temporary.name)
    source = base / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(source, filter="data")
    # ensurepip is bundled, offline; historical install smoke requires real pip.
    environment = base / "venv"
    venv.EnvBuilder(with_pip=True, symlinks=True).create(environment)
    return source, environment / "bin/python"


def assert_historical_phase7(selector: str) -> None:
    source, python = _snapshot()
    if not selector.startswith("tests."):
        selector = "tests." + selector
    completed = subprocess.run(
        [str(python), "-B", "-m", "unittest", selector, "-v"],
        cwd=source,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        },
        capture_output=True, text=True, timeout=120, check=False,
    )
    if completed.returncode != 0 or "Ran 1 test" not in completed.stderr:
        raise AssertionError(
            f"historical Phase 7 contract failed at {PHASE7_COMMIT}: {selector}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
