#!/usr/bin/env python3
"""Run deterministic Phase 6 handoff/payment properties."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_followup.properties import (
    FollowupPropertyReport,
    _merge_followup_property_reports,
    _run_followup_property_range,
)

_MIN_GATE_CASES = 20_000
_PHASE = "phase-06-handoff-and-payments"
_SHARD_CASES = 1_000
_MAX_WORKERS = 4
_DEEP_AUDIT_STRIDE = 16


def _partition_ranges(*, cases: int, shard_cases: int) -> tuple[tuple[int, int], ...]:
    if type(cases) is not int or cases < 1:
        raise ValueError("cases must be a positive exact integer")
    if type(shard_cases) is not int or shard_cases < 1:
        raise ValueError("shard_cases must be a positive exact integer")
    return tuple(
        (start, min(shard_cases, cases - start))
        for start in range(0, cases, shard_cases)
    )


def _run_shard(spec: tuple[int, int, int, bool]) -> dict[str, object]:
    start, cases, seed, deep_consistency = spec
    report = _run_followup_property_range(
        start=start,
        cases=cases,
        seed=seed,
        deep_consistency=deep_consistency,
    )
    return {
        "start": report.start,
        "cases": report.cases,
        "seed": report.seed,
        "rows": tuple(row.to_dict() for row in report.rows),
        "audits": tuple(audit.to_dict() for audit in report.audits),
        "violations": report.violations,
    }


def _report_from_shard_payload(payload: dict[str, object]) -> FollowupPropertyReport:
    from reservation_followup.properties import (
        FollowupPropertyAudit,
        FollowupPropertyRow,
    )

    if set(payload) != {"start", "cases", "seed", "rows", "audits", "violations"}:
        raise ValueError("internal property shard payload has a divergent schema")
    return FollowupPropertyReport(
        start=payload["start"],
        cases=payload["cases"],
        seed=payload["seed"],
        rows=tuple(FollowupPropertyRow.from_dict(row) for row in payload["rows"]),
        audits=tuple(
            FollowupPropertyAudit.from_dict(audit) for audit in payload["audits"]
        ),
        violations=tuple(payload["violations"]),
    )


def run_sharded_followup_properties(
    *,
    cases: int,
    seed: int,
    max_workers: int | None = None,
    shard_cases: int = _SHARD_CASES,
) -> FollowupPropertyReport:
    ranges = _partition_ranges(cases=cases, shard_cases=shard_cases)
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if max_workers is None:
        max_workers = min(_MAX_WORKERS, os.cpu_count() or 1)
    if type(max_workers) is not int or not 1 <= max_workers <= _MAX_WORKERS:
        raise ValueError(f"max_workers must be an exact integer from 1 to {_MAX_WORKERS}")
    specs = tuple(
        (start, count, seed, shard_index % _DEEP_AUDIT_STRIDE == 0)
        for shard_index, (start, count) in enumerate(ranges)
    )
    if len(specs) == 1:
        reports = (_report_from_shard_payload(_run_shard(specs[0])),)
    else:
        with ProcessPoolExecutor(max_workers=min(max_workers, len(specs))) as executor:
            reports = tuple(
                _report_from_shard_payload(payload)
                for payload in executor.map(_run_shard, specs)
            )
    return _merge_followup_property_reports(reports, cases=cases, seed=seed)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Phase 6 handoff/payment operational properties"
    )
    parser.add_argument("--cases", type=int, default=_MIN_GATE_CASES)
    parser.add_argument("--seed", type=int, default=2_026_071_906)
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
    report = run_sharded_followup_properties(cases=args.cases, seed=args.seed)
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
