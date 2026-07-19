#!/usr/bin/env python3
"""Run the closed Phase 4 mutation catalog in disposable repository copies."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

PHASE = "phase-04-single-summary-and-confirmation"
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Mutant:
    name: str
    path: str
    old: str
    new: str
    test: str


MUTANTS = (
    Mutant(
        name="allow_same_timestamp_confirmation",
        path="reservation_confirmation/binding.py",
        old="    if received_at <= context.presented_at:\n",
        new="    if received_at < context.presented_at:\n",
        test="tests.test_phase4_replays.BindingReplayTests.test_missing_state_wrong_hash_same_time_and_classifier_error_are_eventless",
    ),
    Mutant(
        name="trust_wrong_content_hash",
        path="reservation_confirmation/binding.py",
        old="    if rendered.content_hash != content_hash:\n",
        new="    if False and rendered.content_hash != content_hash:\n",
        test="tests.test_phase4_replays.BindingReplayTests.test_context_is_recomputed_from_exact_persisted_summary_artifact",
    ),
    Mutant(
        name="trust_tampered_summary_artifact",
        path="reservation_confirmation/binding.py",
        old='''    if (\n        state.summary.summary_event_id != summary_event_id\n        or state.summary.outbox_message_id != outbox_message_id\n    ):\n''',
        new='''    if False and (\n        state.summary.summary_event_id != summary_event_id\n        or state.summary.outbox_message_id != outbox_message_id\n    ):\n''',
        test="tests.test_phase4_properties.Phase4PropertyTests.test_properties_cover_authorization_and_fail_closed_directions",
    ),
    Mutant(
        name="bind_wrong_draft_version",
        path="reservation_confirmation/binding.py",
        old="        target_draft_version=context.draft_version,\n",
        new="        target_draft_version=context.draft_version + 1,\n",
        test="tests.test_phase4_replays.BindingReplayTests.test_valid_contextual_acceptance_creates_one_command_for_both_providers",
    ),
    Mutant(
        name="bind_wrong_subject_signature",
        path="reservation_confirmation/binding.py",
        old="        subject_signature=context.subject_signature,\n",
        new='        subject_signature="f" * 64,\n',
        test="tests.test_phase4_replays.BindingReplayTests.test_valid_contextual_acceptance_creates_one_command_for_both_providers",
    ),
    Mutant(
        name="emit_event_after_classifier_failure",
        path="reservation_confirmation/binding.py",
        old="    if boundary_failures.intersection(candidate.evidence_codes):\n",
        new="    if False and boundary_failures.intersection(candidate.evidence_codes):\n",
        test="tests.test_phase4_replays.BindingReplayTests.test_missing_state_wrong_hash_same_time_and_classifier_error_are_eventless",
    ),
    Mutant(
        name="accept_without_classification_context",
        path="reservation_confirmation/classifier.py",
        old="        if item.context is None:\n",
        new="        if False and item.context is None:\n",
        test="tests.test_phase4_classifier.ClassifierTests.test_context_is_required_even_for_explicit_acceptance",
    ),
    Mutant(
        name="choose_signal_when_mixed",
        path="reservation_confirmation/classifier.py",
        old="        if len(signals) != 1:\n",
        new="        if not signals:\n",
        test="tests.test_phase4_classifier.ClassifierTests.test_mixed_signals_fail_closed",
    ),
    Mutant(
        name="omit_addons_from_summary_total",
        path="reservation_confirmation/renderer.py",
        old="    total = Money(amount=component_total + add_on_total, currency=currency)\n",
        new="    total = Money(amount=component_total, currency=currency)\n",
        test="tests.test_phase4_renderer.RendererTests.test_pt_package_summary_is_exact_and_has_no_effect_claim",
    ),
    Mutant(
        name="remove_private_identifier_guard",
        path="reservation_confirmation/renderer.py",
        old='''    if leaked:\n        raise ValueError("rendered summary contains a private domain identifier")\n''',
        new="",
        test="tests.test_phase4_renderer.RendererTests.test_renderer_rejects_public_label_equal_to_private_identifier",
    ),
    Mutant(
        name="unbind_artifact_identity_from_locale_and_content",
        path="reservation_confirmation/presentation.py",
        old='''        rendered.locale.value,\n        str(rendered.renderer_version),\n        rendered.content_hash,\n''',
        new='''        "locale:omitted",\n        str(rendered.renderer_version),\n        "content:omitted",\n''',
        test="tests.test_phase4_renderer.RendererTests.test_summary_artifact_identity_binds_locale_and_rendered_content",
    ),
    Mutant(
        name="keep_summary_armed_after_adjustment",
        path="reservation_domain/reducer.py",
        old="            state=AwaitingAdjustmentState(\n",
        new="            state=state if True else AwaitingAdjustmentState(\n",
        test="tests.test_phase4_adjustment_state.AdjustmentStateTests.test_adjustment_decision_disarms_presented_summary",
    ),
    Mutant(
        name="allow_noop_adjustment_version",
        path="reservation_domain/reducer.py",
        old="    if draft.subject_signature == state.draft.subject_signature:\n",
        new="    if False and draft.subject_signature == state.draft.subject_signature:\n",
        test="tests.test_phase4_adjustment_state.AdjustmentStateTests.test_noop_adjustment_does_not_create_new_version",
    ),
    Mutant(
        name="accept_stale_draft_version",
        path="reservation_domain/reducer.py",
        old="        event.target_draft_version == state.draft.version\n",
        new="        state.draft.version == state.draft.version\n",
        test="tests.test_phase2_domain.ReducerContractTests.test_mismatched_version_or_signature_never_emits_command",
    ),
    Mutant(
        name="accept_stale_subject_signature",
        path="reservation_domain/reducer.py",
        old='''        event.target_draft_version == state.draft.version\n        and event.subject_signature == state.draft.subject_signature\n        and event.subject_signature == state.summary.subject_signature\n''',
        new='''        event.target_draft_version == state.draft.version\n        and state.draft.subject_signature == state.draft.subject_signature\n        and state.summary.subject_signature == state.summary.subject_signature\n''',
        test="tests.test_phase2_domain.ReducerContractTests.test_mismatched_version_or_signature_never_emits_command",
    ),
)


def run_mutants(
    *, root: Path = ROOT, selected_names: tuple[str, ...] | None = None
) -> dict[str, Any]:
    catalog = {mutant.name: mutant for mutant in MUTANTS}
    if selected_names is None:
        selected = MUTANTS
    else:
        unknown = sorted(set(selected_names) - set(catalog))
        if unknown:
            raise ValueError(f"unknown mutants: {unknown}")
        selected = tuple(catalog[name] for name in selected_names)
    results = [_run_one(root=root, mutant=mutant) for mutant in selected]
    return {
        "schema_version": 1,
        "phase": PHASE,
        "scope": "temporary copies only; working repository unchanged",
        "mutants": results,
        "mutant_count": len(results),
        "all_killed": bool(results) and all(item["killed"] for item in results),
    }


def _run_one(*, root: Path, mutant: Mutant) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"phase4-mutant-{mutant.name}-") as temp:
        copy_root = Path(temp) / "repo"
        copy_root.mkdir()
        for directory in (
            "reservation_domain",
            "reservation_lookup",
            "reservation_confirmation",
            "scripts",
            "tests",
        ):
            shutil.copytree(
                root / directory,
                copy_root / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        target = copy_root / mutant.path
        source = target.read_text(encoding="utf-8")
        target_count = source.count(mutant.old)
        if target_count != 1:
            return {
                "name": mutant.name,
                "path": mutant.path,
                "test": mutant.test,
                "exit_code": -1,
                "killed": False,
                "error": f"mutation target count was {target_count}, expected 1",
            }
        target.write_text(source.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", mutant.test, "-v"],
                cwd=copy_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            exit_code = completed.returncode
            error = None
        except subprocess.TimeoutExpired:
            exit_code = -2
            error = "mutant test timed out"
        result: dict[str, Any] = {
            "name": mutant.name,
            "path": mutant.path,
            "test": mutant.test,
            "exit_code": exit_code,
            "killed": exit_code > 0,
        }
        if error is not None:
            result["error"] = error
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    try:
        selected = tuple(args.only) if args.only else None
        report = run_mutants(root=ROOT, selected_names=selected)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.write is not None:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["all_killed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
