from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_confirmation import run_phase4_properties

_MIN_GATE_CASES = 50_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 4 confirmation properties")
    parser.add_argument("--cases", type=int, default=_MIN_GATE_CASES)
    parser.add_argument("--seed", type=int, default=20_260_719)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    if args.cases <= 0:
        parser.error("--cases must be positive")
    if not args.smoke and args.cases < _MIN_GATE_CASES:
        parser.error(
            f"gate mode requires --cases >= {_MIN_GATE_CASES}; use --smoke for smaller runs"
        )

    report = run_phase4_properties(cases=args.cases, seed=args.seed)
    payload = {
        "schema_version": 1,
        "phase": "phase-04-single-summary-and-confirmation",
        "mode": "smoke" if args.smoke else "gate",
        "configuration": {
            "cases": args.cases,
            "seed": args.seed,
            "minimum_gate_cases": _MIN_GATE_CASES,
        },
        "report": report.to_dict(),
        "result": "passed" if report.passed else "failed",
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
