#!/usr/bin/env python3
"""Validate Phase 0 planning artifacts without external dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "refactor" / "evidence" / "phase-00"

REQUIRED = (
    "README.md",
    "AGENTS.md",
    ".gitignore",
    "docs/refactor/README.md",
    "docs/refactor/00-charter.md",
    "docs/refactor/01-baseline.md",
    "docs/refactor/02-failure-taxonomy.md",
    "docs/refactor/03-target-architecture.md",
    "docs/refactor/04-phased-delivery-plan.md",
    "docs/refactor/05-validation-and-rollout.md",
    "docs/refactor/06-risk-register.md",
    "docs/refactor/phases/phase-00-baseline-and-governance.md",
    "docs/refactor/evidence/README.md",
    "docs/refactor/evidence/phase-00/baseline-manifest.json",
    "docs/refactor/evidence/phase-00/critical-artifact-hashes.json",
    "docs/refactor/evidence/phase-00/source-working-tree-status.txt",
    "docs/refactor/evidence/phase-00/source-diff-stat.txt",
    "docs/refactor/evidence/phase-00/source-diff-numstat.txt",
    "docs/refactor/evidence/phase-00/validation-result.json",
    "docs/refactor/evidence/phase-00/SHA256SUMS",
    "docs/refactor/decisions/README.md",
    "docs/refactor/decisions/0001-incremental-strangler-migration.md",
    "docs/refactor/decisions/0002-single-deterministic-reservation-kernel.md",
    "docs/refactor/decisions/0003-canonical-offer-token.md",
    "docs/refactor/decisions/0004-durable-command-execution.md",
    "docs/refactor/decisions/0005-separate-ledger-and-outbox.md",
    "docs/refactor/decisions/0006-promote-identical-oci-digest.md",
    "scripts/validate_phase0.py",
    ".github/workflows/phase0.yml",
)

SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    "credentialed_url": re.compile(r"https://[^\s/:@]+:[^\s/@]+@"),
    "assigned_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|access[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
    ),
    "brazilian_phone": re.compile(r"\+55\d{10,11}\b"),
    "email_address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
}

TEXT_SUFFIXES = {".md", ".txt", ".json", ".yml", ".yaml", ".py", ""}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_required(failures: list[str]) -> None:
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            failures.append(f"missing required file: {relative}")


def check_git_index(failures: list[str]) -> None:
    """Every required artifact must be tracked or staged, not merely present locally."""
    for relative in REQUIRED:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(f"required file is not tracked/staged: {relative}")


def check_manifest(failures: list[str]) -> dict[str, object]:
    path = EVIDENCE / "baseline-manifest.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"invalid baseline manifest: {exc}")
        return {}
    if manifest.get("schema_version") != 1:
        failures.append("baseline schema_version must be 1")
    if manifest.get("phase") != "phase-00-baseline-and-governance":
        failures.append("baseline phase is invalid")
    source = manifest.get("source_repository") or {}
    counts = source.get("status_counts") or {}
    if sum(int(value) for value in counts.values()) != source.get("status_entry_count"):
        failures.append("working-tree status counts do not match entry count")
    budget = manifest.get("temporal_budget") or {}
    if budget.get("configuration_can_start_write") is not False:
        failures.append("baseline no longer records the observed impossible write budget")
    hashes = manifest.get("critical_artifact_hashes") or []
    if len(hashes) != 16:
        failures.append("expected 16 critical artifact hash records")
    return manifest


def check_sums(failures: list[str]) -> int:
    path = EVIDENCE / "SHA256SUMS"
    if not path.is_file():
        return 0
    checked = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            failures.append(f"malformed SHA256SUMS line {number}")
            continue
        target = ROOT / relative
        if not target.is_file():
            failures.append(f"hash target missing: {relative}")
            continue
        actual = sha256(target)
        if actual != expected:
            failures.append(f"hash mismatch: {relative}")
        checked += 1
    if checked < 5:
        failures.append("SHA256SUMS must cover at least five Phase 0 evidence files")
    return checked


def check_secrets_and_pii(failures: list[str]) -> int:
    scanned = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".gitignore":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(ROOT)
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"possible {name} in {relative}")
        scanned += 1
    return scanned


def check_markdown_links(failures: list[str]) -> int:
    checked = 0
    link_pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    for path in ROOT.rglob("*.md"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for raw in link_pattern.findall(text):
            target = raw.strip().split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                failures.append(f"broken markdown link in {path.relative_to(ROOT)}: {raw}")
            checked += 1
    return checked


def main() -> int:
    failures: list[str] = []
    check_required(failures)
    check_git_index(failures)
    manifest = check_manifest(failures)
    hashes = check_sums(failures)
    scanned = check_secrets_and_pii(failures)
    links = check_markdown_links(failures)
    summary = {
        "status": "failed" if failures else "ok",
        "required_files": len(REQUIRED),
        "evidence_hashes_checked": hashes,
        "text_files_scanned": scanned,
        "relative_links_checked": links,
        "baseline_captured_at": manifest.get("captured_at_utc"),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
