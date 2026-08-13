from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9_-]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+\S+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*\S+"),
)
FORBIDDEN_KEYS = {
    "email",
    "phone",
    "subscriber_id",
    "conversation_id",
    "raw_payload",
    "headers",
    "cookie",
    "password",
    "secret",
    "canonical_url",
    "public_url",
}


def _validate_closed_json(value: object) -> None:
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is list:
        for item in value:
            _validate_closed_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("JSON object keys must be exact strings")
            _validate_closed_json(item)
        return
    raise TypeError("value is outside the closed JSON grammar")


def verify(path: Path) -> tuple[int, list[str]]:
    import sqlite3

    findings: list[str] = []
    if not path.is_absolute() or not path.is_file():
        return 2, ["trace path must be an existing absolute file"]
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    try:
        for table, columns in (
            ("executions", ("execution_id", "lead_id", "terminal_reason")),
            (
                "nodes",
                (
                    "input_summary_json",
                    "output_summary_json",
                    "error_json",
                    "technical_metadata_json",
                ),
            ),
        ):
            names = ",".join(columns)
            for row in connection.execute(f"SELECT {names} FROM {table}"):
                text = "\n".join("" if value is None else str(value) for value in row)
                if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                    findings.append(f"secret-like value in {table}")
                for value in row:
                    if type(value) is not str or not value.startswith(("{", "[")):
                        continue
                    try:
                        decoded = json.loads(value)
                        _validate_closed_json(decoded)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        findings.append(f"invalid closed JSON in {table}")
                        continue
                    stack = [decoded]
                    while stack:
                        current = stack.pop()
                        if type(current) is dict:
                            for key, item in current.items():
                                if key.casefold() in FORBIDDEN_KEYS:
                                    findings.append(f"forbidden key {key} in {table}")
                                stack.append(item)
                        elif type(current) is list:
                            stack.extend(current)
    finally:
        connection.close()
    return (1 if findings else 0), sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    code, findings = verify(args.path)
    if findings:
        print("v2-ops-secret-scan: FAILED")
        for finding in findings:
            print(f"- {finding}")
    else:
        print("v2-ops-secret-scan: OK")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
