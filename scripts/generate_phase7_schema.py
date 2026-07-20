#!/usr/bin/env python3
"""Write or verify deterministic Phase 7 schema artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_boundary.schema import (  # noqa: E402
    SCHEMA_VERSION,
    render_postgresql,
    render_sqlite,
    schema_hash,
)


TARGETS = {
    "sqlite": ROOT / "schemas/phase7/sqlite.sql",
    "postgresql": ROOT / "schemas/phase7/postgresql.sql",
}


def _contents() -> dict[str, str]:
    return {
        "sqlite": render_sqlite(),
        "postgresql": render_postgresql(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    contents = _contents()
    if args.write:
        for dialect, path in TARGETS.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents[dialect], encoding="utf-8")
    else:
        for dialect, path in TARGETS.items():
            if not path.is_file():
                failures.append(f"missing generated artifact: {path.relative_to(ROOT)}")
            elif path.read_text(encoding="utf-8") != contents[dialect]:
                failures.append(f"stale generated artifact: {path.relative_to(ROOT)}")

    result = "passed" if not failures else "failed"
    print(
        json.dumps(
            {
                "artifacts": {
                    dialect: {
                        "path": str(path.relative_to(ROOT)),
                        "sha256": schema_hash(dialect),
                    }
                    for dialect, path in TARGETS.items()
                },
                "failures": failures,
                "mode": "write" if args.write else "check",
                "result": result,
                "schema_version": SCHEMA_VERSION,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
