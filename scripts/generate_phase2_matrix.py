#!/usr/bin/env python3
"""Generate the complete state/event policy matrix for Phase 2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reservation_domain import EVENT_TYPES, STATE_TYPES, transition_matrix


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_domain_manifest() -> dict[str, object]:
    domain = ROOT / "reservation_domain"
    paths = sorted(
        (*domain.glob("*.py"), domain / "README.md"),
        key=lambda item: str(item.relative_to(ROOT)),
    )
    return {
        "schema_version": 1,
        "phase": "phase-02-typed-domain-and-reducer",
        "hash_algorithm": "sha256",
        "state_count": len(STATE_TYPES),
        "event_count": len(EVENT_TYPES),
        "state_event_pairs": len(STATE_TYPES) * len(EVENT_TYPES),
        "files": [
            {
                "path": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in paths
        ],
    }


def render_matrix() -> str:
    matrix = transition_matrix()
    event_tags = [item.TYPE for item in EVENT_TYPES]
    lines = [
        "# Matriz completa estado/evento — Fase 2",
        "",
        "Gerada deterministicamente de `reservation_domain.reducer`.",
        "",
        "- `evaluate`: existe handler; o evento ainda pode ser aplicado ou rejeitado pelas invariantes.",
        "- `ignore`: não existe transição semântica nesse estado; o reducer registra o evento e não emite comando.",
        "- duplicatas são no-op antes da matriz; eventos fora de ordem são rejeitados antes da matriz.",
        "",
        "| Estado | " + " | ".join(event_tags) + " |",
        "|---|" + "|".join("---" for _ in event_tags) + "|",
    ]
    for state_type in STATE_TYPES:
        row = matrix[state_type.TYPE]
        lines.append(
            "| `" + state_type.TYPE + "` | "
            + " | ".join(row[event] for event in event_tags)
            + " |"
        )
    lines.extend(
        [
            "",
            f"Estados discriminados: **{len(STATE_TYPES)}**.",
            f"Eventos discriminados: **{len(EVENT_TYPES)}**.",
            f"Pares com política explícita: **{len(STATE_TYPES) * len(EVENT_TYPES)}**.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    text = render_matrix()
    if args.write:
        target = args.write
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if args.manifest:
        target = args.manifest
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(build_domain_manifest(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
