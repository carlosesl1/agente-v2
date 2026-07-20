#!/usr/bin/env python3
"""Run focused or frozen Phase 7 fault/restart/contention gates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_boundary.faults import run_fault_matrix  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--focused", action="store_true")
    mode.add_argument("--integral", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    frozen = None
    current = None
    if args.integral:
        frozen = os.environ.get("PHASE7_FROZEN_TREE")
        current = subprocess.run(
            ["git", "write-tree"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    report = run_fault_matrix(
        focused=args.focused,
        frozen_tree=frozen,
        current_tree=current,
    )
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps({"faults": len(report.faults), "output": str(target), "passed": report.passed}, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
