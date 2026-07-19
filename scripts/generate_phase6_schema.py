#!/usr/bin/env python3
"""Generate deterministic Phase 6 SQLite and PostgreSQL DDL artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_followup.schema import (  # noqa: E402
    SCHEMA_VERSION,
    render_postgresql,
    render_sqlite,
    schema_hash,
)


def _target(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def _write(path: Path, content: str) -> Path:
    target = _target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic Phase 6 follow-up schema artifacts."
    )
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--postgresql", required=True, type=Path)
    args = parser.parse_args()

    sqlite_target = _target(args.sqlite).resolve()
    postgresql_target = _target(args.postgresql).resolve()
    if sqlite_target == postgresql_target:
        parser.error("--sqlite and --postgresql must be distinct targets")

    sqlite_target = _write(sqlite_target, render_sqlite())
    postgresql_target = _write(postgresql_target, render_postgresql())
    print(
        json.dumps(
            {
                "postgresql": {
                    "path": str(postgresql_target),
                    "sha256": schema_hash("postgresql"),
                },
                "schema_version": SCHEMA_VERSION,
                "sqlite": {
                    "path": str(sqlite_target),
                    "sha256": schema_hash("sqlite"),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
