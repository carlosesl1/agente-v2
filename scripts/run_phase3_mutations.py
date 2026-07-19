#!/usr/bin/env python3
"""Run the closed Phase 3 mutation catalog in disposable repository copies."""

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

PHASE = "phase-03-lookups-and-offer-snapshots"
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
        name="include_label_in_offer_identity",
        path="reservation_lookup/identity.py",
        old='''        "provider": provider.value,\n        "provider_ref": offer.provider_ref,\n''',
        new='''        "provider": provider.value,\n        "public_label": offer.public_label,\n        "provider_ref": offer.provider_ref,\n''',
        test="tests.test_phase3_lookup_types.OpaqueIdentityTests.test_label_and_lookup_provenance_do_not_change_offer_id",
    ),
    Mutant(
        name="exclude_total_from_offer_identity",
        path="reservation_lookup/identity.py",
        old='''        "total": {\n            "amount": format(offer.total.amount, "f"),\n            "currency": offer.total.currency,\n        },\n''',
        new="",
        test="tests.test_phase3_lookup_types.OpaqueIdentityTests.test_every_executable_offer_mutation_changes_offer_id",
    ),
    Mutant(
        name="exclude_provider_from_offer_identity",
        path="reservation_lookup/identity.py",
        old='''        "provider": provider.value,\n        "provider_ref": offer.provider_ref,\n''',
        new='''        "provider_ref": offer.provider_ref,\n''',
        test="tests.test_phase3_lookup_types.OpaqueIdentityTests.test_every_executable_offer_mutation_changes_offer_id",
    ),
    Mutant(
        name="accept_provider_ref_as_selection",
        path="reservation_lookup/selection.py",
        old='''    if not _OFFER_ID_RE.fullmatch(offer_id):\n        raise SelectionRejected(SelectionErrorCode.OFFER_ID_NOT_FOUND)\n    matches = tuple(offer for offer in result.offers if offer.offer_id == offer_id)\n''',
        new='''    if _OFFER_ID_RE.fullmatch(offer_id):\n        matches = tuple(offer for offer in result.offers if offer.offer_id == offer_id)\n    else:\n        matches = tuple(offer for offer in result.offers if offer.provider_ref == offer_id)\n''',
        test="tests.test_phase3_selection.ExactOfferSelectionTests.test_label_provider_ref_index_random_and_wrong_types_never_select",
    ),
    Mutant(
        name="accept_expired_lookup",
        path="reservation_lookup/selection.py",
        old='''    if not result.evidence.is_fresh(at):\n''',
        new='''    if False and not result.evidence.is_fresh(at):\n''',
        test="tests.test_phase3_selection.ExactOfferSelectionTests.test_negative_uncertain_and_expired_lookup_fail_closed",
    ),
    Mutant(
        name="choose_first_duplicate_offer",
        path="reservation_lookup/selection.py",
        old='''    if len(matches) != 1:\n''',
        new='''    if False and len(matches) != 1:\n''',
        test="tests.test_phase3_selection.ExactOfferSelectionTests.test_duplicate_matches_fail_closed_instead_of_choosing_first",
    ),
    Mutant(
        name="tolerate_missing_cloudbeds_rate_plan",
        path="reservation_lookup/cloudbeds.py",
        old='''        if rate_plan_id not in rate_plan_ids:\n            raise ProviderSchemaError(f"room_{index}_rate_plan_not_found")\n''',
        new="",
        test="tests.test_phase3_cloudbeds_adapter.CloudbedsFailClosedTests.test_missing_rate_plan_is_uncertain",
    ),
    Mutant(
        name="classify_cloudbeds_schema_error_as_negative",
        path="reservation_lookup/cloudbeds.py",
        old='''                status=LookupStatus.UNCERTAIN,\n                failures=(LookupFailure(code="schema_error", detail=str(exc)),),\n''',
        new='''                status=LookupStatus.NEGATIVE,\n                failures=(),\n''',
        test="tests.test_phase3_cloudbeds_adapter.CloudbedsFailClosedTests.test_partial_stay_is_uncertain",
    ),
    Mutant(
        name="allow_non_get_read_request",
        path="reservation_lookup/types.py",
        old='''        if type(self.method) is not str or self.method != "GET":\n''',
        new='''        if False:\n''',
        test="tests.test_phase3_lookup_types.ReadBoundaryTypeTests.test_read_request_is_get_only_relative_and_canonical",
    ),
    Mutant(
        name="allow_bokun_metadata_id_mismatch",
        path="reservation_lookup/bokun.py",
        old='''    if product_id != expected_product_id:\n''',
        new='''    if False and product_id != expected_product_id:\n''',
        test="tests.test_phase3_bokun_adapter.BokunFailClosedTests.test_metadata_product_id_mismatch_is_uncertain",
    ),
    Mutant(
        name="remove_request_dot_segment_guard",
        path="reservation_lookup/types.py",
        old='''            or any(segment in {"", ".", ".."} for segment in path_segments)\n''',
        new="",
        test="tests.test_phase3_lookup_types.ReadBoundaryTypeTests.test_read_request_is_get_only_relative_and_canonical",
    ),
    Mutant(
        name="remove_response_deep_freeze",
        path="reservation_lookup/types.py",
        old='''        object.__setattr__(self, "body", _freeze_json(detached))\n''',
        new="",
        test="tests.test_phase3_lookup_types.ReadBoundaryTypeTests.test_response_body_is_deeply_immutable_and_detached",
    ),
    Mutant(
        name="remove_provider_ref_namespace_binding",
        path="reservation_lookup/types.py",
        old='''            if not offer.provider_ref.startswith(\n                f"{self.provenance.provider.value}."\n            ):\n                raise ValueError("offer provider_ref namespace does not match provider")\n''',
        new="",
        test="tests.test_phase3_lookup_types.LookupResultContractTests.test_result_rejects_provider_ref_and_service_namespace_mismatch",
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

    results: list[dict[str, Any]] = []
    for mutant in selected:
        result = _run_one(root=root, mutant=mutant)
        results.append(result)
    return {
        "schema_version": 1,
        "phase": PHASE,
        "scope": "temporary copies only; working repository unchanged",
        "mutants": results,
        "mutant_count": len(results),
        "all_killed": bool(results) and all(item["killed"] for item in results),
    }


def _run_one(*, root: Path, mutant: Mutant) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"phase3-mutant-{mutant.name}-") as temp:
        copy_root = Path(temp) / "repo"
        copy_root.mkdir()
        for directory in ("reservation_domain", "reservation_lookup", "tests"):
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
