#!/usr/bin/env python3
"""Run deterministic Phase 7 property sequences."""

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

from reservation_boundary.properties import (  # noqa: E402
    PROPERTY_CASES,
    PROPERTY_SEED,
    run_property_sequences,
)


def _trees(integral: bool) -> tuple[str | None, str | None]:
    if not integral:
        return None, None
    frozen = os.environ.get("PHASE7_FROZEN_TREE")
    current = subprocess.run(
        ["git", "write-tree"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    return frozen, current


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=int, default=PROPERTY_CASES)
    parser.add_argument("--seed", type=int, default=PROPERTY_SEED)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    frozen, current = _trees(args.cases >= PROPERTY_CASES)
    report = run_property_sequences(
        seed=args.seed,
        cases=args.cases,
        frozen_tree=frozen,
        current_tree=current,
    )
    target = args.output.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    print(json.dumps({"output": str(target), "passed": report.passed, "total": report.total}, sort_keys=True))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
