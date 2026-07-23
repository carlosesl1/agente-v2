#!/usr/bin/env python3
"""Run the three mandatory V2 fake-provider qualification scenarios."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time


SCENARIOS = (
    "tests/test_v2_e2e.py::test_lodging_stripe_qualification_has_one_effect_per_idempotency_key",
    "tests/test_v2_e2e.py::test_activity_pix_qualification_uses_knowledge_and_no_stripe",
    "tests/test_v2_e2e.py::test_package_wise_qualification_keeps_components_and_units_separate",
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *SCENARIOS],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    report = {
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": completed.returncode,
        "providers": "fake_only",
        "real_effects": False,
        "scenarios": [
            "lodging_stripe",
            "activity_pix",
            "package_wise",
        ],
        "status": "passed" if completed.returncode == 0 else "failed",
        "test_output": completed.stdout.strip(),
    }
    if completed.stderr.strip():
        report["test_stderr"] = completed.stderr.strip()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
