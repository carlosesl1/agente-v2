#!/usr/bin/env python3
"""Run deterministic Phase 2 property sequences and optionally save evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_domain import run_property_sequences


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequences", type=int, default=100_000)
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20_260_718)
    parser.add_argument("--write", type=Path)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="allow a workload below the mandatory 100k x 20 evidence gate",
    )
    args = parser.parse_args()
    if not args.smoke and (args.sequences < 100_000 or args.max_events < 20):
        parser.error("gate requires at least 100000 sequences and 20 events")
    report = run_property_sequences(
        sequences=args.sequences,
        max_events=args.max_events,
        seed=args.seed,
    )
    gate_failures = list(report.violations)
    if not args.smoke:
        required_positive = {
            "authorized_accepts": report.authorized_accepts,
            "out_of_order_probes": report.out_of_order_probes,
            "lookup_positive_cases": report.lookup_positive_cases,
            "lookup_negative_cases": report.lookup_negative_cases,
            "lookup_expired_cases": report.lookup_expired_cases,
            "lookup_unavailable_cases": report.lookup_unavailable_cases,
            "lookup_multi_offer_cases": report.lookup_multi_offer_cases,
        }
        gate_failures.extend(
            f"coverage counter {name} must be positive"
            for name, value in required_positive.items()
            if value < 1
        )
    payload = {
        "schema_version": 1,
        "phase": "phase-02-typed-domain-and-reducer",
        "mode": "smoke" if args.smoke else "gate",
        **asdict(report),
        "gate_failures": gate_failures,
        "result": "passed" if not gate_failures else "failed",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write:
        target = args.write
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if not gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
