#!/usr/bin/env python3
"""Run deterministic Phase 5 operational properties."""

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

from reservation_execution.properties import (
    Phase5PropertyReport,
    _merge_phase5_property_reports,
    _run_phase5_property_range,
)

_MIN_GATE_CASES = 20_000
_PHASE = "phase-05-durable-command-execution"
_SHARD_CASES = 32
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
    return _run_phase5_property_range(
        start=start,
        cases=cases,
        seed=seed,
        deep_consistency=deep_consistency,
    ).to_dict()


def _report_from_dict(payload: dict[str, object]) -> Phase5PropertyReport:
    values = dict(payload)
    values.pop("passed")
    values["violations"] = tuple(values["violations"])
    return Phase5PropertyReport(**values)


def run_sharded_phase5_properties(
    *,
    cases: int,
    seed: int,
    max_workers: int | None = None,
    shard_cases: int = _SHARD_CASES,
) -> Phase5PropertyReport:
    ranges = _partition_ranges(cases=cases, shard_cases=shard_cases)
    if type(seed) is not int:
        raise TypeError("seed must be an exact integer")
    if max_workers is None:
        max_workers = min(_MAX_WORKERS, os.cpu_count() or 1)
    if type(max_workers) is not int or max_workers < 1:
        raise ValueError("max_workers must be a positive exact integer")
    specs = tuple(
        (start, count, seed, shard_index % _DEEP_AUDIT_STRIDE == 0)
        for shard_index, (start, count) in enumerate(ranges)
    )
    if len(specs) == 1:
        reports = (_report_from_dict(_run_shard(specs[0])),)
    else:
        with ProcessPoolExecutor(
            max_workers=min(max_workers, len(specs))
        ) as executor:
            reports = tuple(
                _report_from_dict(payload)
                for payload in executor.map(_run_shard, specs)
            )
    return _merge_phase5_property_reports(reports, cases=cases, seed=seed)


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

    report = run_sharded_phase5_properties(cases=args.cases, seed=args.seed)
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
