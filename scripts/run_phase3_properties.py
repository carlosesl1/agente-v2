from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_lookup.properties import run_lookup_properties

_MIN_GATE_CASES = 50_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Phase 3 lookup properties")
    parser.add_argument("--cases", type=int, default=_MIN_GATE_CASES)
    parser.add_argument("--seed", type=int, default=20_260_718)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    if args.cases <= 0:
        parser.error("--cases must be positive")
    if not args.smoke and args.cases < _MIN_GATE_CASES:
        parser.error(
            f"gate mode requires --cases >= {_MIN_GATE_CASES}; use --smoke for smaller runs"
        )

    report = run_lookup_properties(cases=args.cases, seed=args.seed)
    expected_case_counters = (
        report.positive_authorizations,
        report.label_equivalence_cases,
        report.executable_mutation_cases,
        report.expired_cases,
        report.zero_match_cases,
        report.multiple_match_cases,
    )
    passed = (
        all(value == args.cases for value in expected_case_counters)
        and sum(report.mutation_counts.values()) == args.cases
        and all(value > 0 for value in report.mutation_counts.values())
        and report.false_authorizations == 0
        and report.missed_invalidations == 0
        and report.unexpected_exceptions == 0
        and report.violations == ()
    )
    payload = {
        "schema_version": 1,
        "phase": "phase-03-lookups-and-offer-snapshots",
        "mode": "smoke" if args.smoke else "gate",
        "configuration": {
            "cases": args.cases,
            "seed": args.seed,
            "minimum_gate_cases": _MIN_GATE_CASES,
        },
        "report": report.to_dict(),
        "result": "passed" if passed else "failed",
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
