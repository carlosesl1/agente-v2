#!/usr/bin/env python3
"""Run deterministic Phase 5 operational properties."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_execution.properties import run_phase5_properties

_MIN_GATE_CASES = 20_000
_PHASE = "phase-05-durable-command-execution"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 5 operational properties")
    parser.add_argument("--cases", type=int, default=_MIN_GATE_CASES)
    parser.add_argument("--seed", type=int, default=2_026_071_905)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    if args.cases <= 0:
        parser.error("--cases must be positive")
    if not args.smoke and args.cases < _MIN_GATE_CASES:
        parser.error(
            f"gate mode requires --cases >= {_MIN_GATE_CASES}; "
            "use --smoke for smaller runs"
        )

    report = run_phase5_properties(cases=args.cases, seed=args.seed)
    payload = {
        "schema_version": 1,
        "phase": _PHASE,
        "mode": "smoke" if args.smoke else "gate",
        "configuration": {
            "cases": args.cases,
            "minimum_gate_cases": _MIN_GATE_CASES,
            "seed": args.seed,
        },
        "result": "passed" if report.passed else "failed",
        "report": report.to_dict(),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
