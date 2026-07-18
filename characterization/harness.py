#!/usr/bin/env python3
"""Deterministic, side-effect-free incident characterization harness.

This module intentionally does not import the legacy application. It replays
sanitized, source-backed causal witnesses so Phase 1 can describe the failures
that the new domain must prevent without executing production code or providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
INCIDENT_IDS = frozenset(f"F{number:02d}" for number in range(1, 23))
CLASSIFICATIONS = frozenset({"reproduced", "contract_characterized"})
SCENARIO_KEYS = frozenset(
    {
        "schema_version",
        "incident_id",
        "case_id",
        "classification",
        "title",
        "initial_state",
        "clock_utc",
        "safety",
        "manychat_fixture",
        "provider_fixture",
        "source_refs",
        "trace",
        "expected_violations",
    }
)
TRACE_KINDS = frozenset(
    {
        "bootstrap",
        "command_created",
        "composite_outcome",
        "concurrency",
        "config",
        "confirmation_received",
        "crash",
        "dispatch",
        "future_promise",
        "guard_blocked",
        "handoff",
        "inbound",
        "lookup_use",
        "phase_armed",
        "projection",
        "provider_contract",
        "provider_target",
        "public_reply",
        "readiness",
        "redaction",
        "runtime_attestation",
        "selection",
        "signature",
        "summary_presented",
        "test_fixture",
        "tool_surface",
    }
)
FORBIDDEN_INITIAL_KEYS = frozenset(
    {
        "selected_lodging_option",
        "selected_tour_option",
        "offer_id",
        "room_type_id",
        "tour_product_id",
        "reservation_confirmation_phase",
        "package_confirmation_phase",
        "single_service_confirmation_candidate",
        "subject_signature",
        "final_summary_accepted",
        "lead_confirmed",
        "reservation_command",
        "provider_outcome",
    }
)
FORBIDDEN_CAPABILITIES = frozenset(
    {"network", "provider_reads", "provider_writes", "message_delivery", "database"}
)
FAULT_POINTS = frozenset(
    {
        "before_event_persist",
        "after_event_before_command",
        "after_command_before_claim",
        "after_claim_before_socket",
        "after_socket_response_lost",
        "after_provider_before_outcome",
        "after_outcome_before_outbox",
        "during_public_delivery",
    }
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
BRAZILIAN_PHONE_RE = re.compile(r"\+55\d{10,11}\b")
TOKEN_RE = re.compile(r"\b(?:gh[pousr]_|sk-)[A-Za-z0-9_-]{20,}\b")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


class ScenarioValidationError(ValueError):
    """Raised when a characterization scenario violates the Phase 1 contract."""


@dataclass(frozen=True)
class ReplayResult:
    incident_id: str
    case_id: str
    classification: str
    violations: tuple[str, ...]
    metrics: Mapping[str, int]
    fault_points: tuple[str, ...]


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioValidationError(message)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioValidationError(f"invalid JSON at {path}: {exc}") from exc
    _require(isinstance(value, dict), f"top-level JSON must be an object: {path}")
    return value


def _validate_no_sensitive_literals(value: Any, *, context: str) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for name, pattern in {
        "email": EMAIL_RE,
        "Brazilian phone": BRAZILIAN_PHONE_RE,
        "token": TOKEN_RE,
        "private key": PRIVATE_KEY_RE,
    }.items():
        _require(pattern.search(serialized) is None, f"possible {name} in {context}")


def _validate_fixture_reference(root: Path, relative: str, *, expected_prefix: str) -> Path:
    _require(bool(relative), f"missing fixture reference for {expected_prefix}")
    _require(not Path(relative).is_absolute(), f"absolute fixture path is forbidden: {relative}")
    _require(relative.startswith(expected_prefix), f"fixture outside {expected_prefix}: {relative}")
    resolved = (root / relative).resolve()
    _require(root.resolve() in resolved.parents, f"fixture escapes characterization root: {relative}")
    _require(resolved.is_file(), f"fixture does not exist: {relative}")
    fixture = _load_json(resolved)
    _require(fixture.get("schema_version") == SCHEMA_VERSION, f"fixture schema mismatch: {relative}")
    _require(fixture.get("synthetic") is True, f"fixture must be explicitly synthetic: {relative}")
    _validate_no_sensitive_literals(fixture, context=relative)
    return resolved


def validate_scenario(scenario: Mapping[str, Any], *, characterization_root: Path) -> None:
    incident_id = str(scenario.get("incident_id") or "")
    case_id = str(scenario.get("case_id") or "")
    _require(scenario.get("schema_version") == SCHEMA_VERSION, f"{case_id}: schema_version must be 1")
    unknown_keys = set(scenario) - set(SCENARIO_KEYS)
    _require(not unknown_keys, f"{case_id}: unknown scenario keys: {sorted(unknown_keys)}")
    _require(incident_id in INCIDENT_IDS, f"{case_id}: invalid incident_id {incident_id!r}")
    _require(bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", case_id)), f"invalid case_id: {case_id!r}")
    classification = str(scenario.get("classification") or "")
    _require(classification in CLASSIFICATIONS, f"{case_id}: invalid classification")
    _require(bool(str(scenario.get("title") or "").strip()), f"{case_id}: title is required")

    initial_state = scenario.get("initial_state")
    _require(initial_state == {}, f"{case_id}: every replay must start with exactly empty state")
    forbidden = FORBIDDEN_INITIAL_KEYS.intersection(_walk_keys(initial_state))
    _require(not forbidden, f"{case_id}: canonical state was preseeded: {sorted(forbidden)}")

    safety = scenario.get("safety")
    _require(isinstance(safety, Mapping), f"{case_id}: safety object is required")
    for capability in FORBIDDEN_CAPABILITIES:
        _require(safety.get(capability) is False, f"{case_id}: capability must be false: {capability}")

    clock = str(scenario.get("clock_utc") or "")
    try:
        parsed_clock = datetime.fromisoformat(clock.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScenarioValidationError(f"{case_id}: invalid clock_utc") from exc
    _require(parsed_clock.tzinfo is not None, f"{case_id}: clock_utc must be timezone-aware")

    manychat_path = _validate_fixture_reference(
        characterization_root,
        str(scenario.get("manychat_fixture") or ""),
        expected_prefix="fixtures/manychat/",
    )
    manychat = _load_json(manychat_path)
    events = manychat.get("events")
    _require(isinstance(events, list) and events, f"{case_id}: ManyChat fixture needs events")
    _require(all(isinstance(item, Mapping) for item in events), f"{case_id}: invalid ManyChat event")
    _require(all(item.get("event_id") for item in events), f"{case_id}: event_id is required")
    _require(all(item.get("contact_ref") for item in events), f"{case_id}: contact_ref is required")

    provider_fixture = scenario.get("provider_fixture")
    if provider_fixture is not None:
        _validate_fixture_reference(
            characterization_root,
            str(provider_fixture),
            expected_prefix="fixtures/provider/",
        )

    refs = scenario.get("source_refs")
    _require(isinstance(refs, list) and refs, f"{case_id}: source_refs are required")
    for ref in refs:
        _require(isinstance(ref, Mapping), f"{case_id}: invalid source_ref")
        path = str(ref.get("path") or "")
        _require(bool(path) and not Path(path).is_absolute(), f"{case_id}: source path must be relative")
        _require(".." not in Path(path).parts, f"{case_id}: source path escapes repository")
        symbols = ref.get("symbols")
        _require(isinstance(symbols, list) and symbols, f"{case_id}: source symbols are required")
        _require(bool(str(ref.get("evidence") or "").strip()), f"{case_id}: source evidence is required")

    trace = scenario.get("trace")
    _require(isinstance(trace, list) and trace, f"{case_id}: trace is required")
    sequences = [item.get("seq") for item in trace if isinstance(item, Mapping)]
    _require(len(sequences) == len(trace), f"{case_id}: each trace item must be an object")
    _require(sequences == list(range(1, len(trace) + 1)), f"{case_id}: trace seq must be contiguous")
    _require(trace[0].get("kind") == "inbound", f"{case_id}: first trace item must be inbound")
    unknown_kinds = {str(item.get("kind") or "") for item in trace} - set(TRACE_KINDS)
    _require(not unknown_kinds, f"{case_id}: unknown trace kinds: {sorted(unknown_kinds)}")
    fixture_event_ids = [str(item.get("event_id")) for item in events]
    trace_event_ids = [
        str(item.get("event_id")) for item in trace if item.get("kind") == "inbound"
    ]
    _require(
        str(trace[0].get("event_id")) == fixture_event_ids[0],
        f"{case_id}: trace must start from the first fixture event",
    )
    _require(
        all(event_id in fixture_event_ids for event_id in trace_event_ids),
        f"{case_id}: every inbound trace event must exist in the fixture",
    )

    expected = scenario.get("expected_violations")
    _require(isinstance(expected, list) and expected, f"{case_id}: expected_violations are required")
    _require(all(isinstance(item, str) and item for item in expected), f"{case_id}: invalid violation code")
    _require(len(set(expected)) == len(expected), f"{case_id}: duplicate expected violation")
    _validate_no_sensitive_literals(scenario, context=case_id)


def _aggregate_outcome(leaves: Iterable[str]) -> str:
    values = tuple(leaves)
    if "called_unknown" in values:
        return "called_unknown"
    if "effect_confirmed" in values:
        return "effect_confirmed"
    if "called_no_effect" in values:
        return "called_no_effect"
    return "not_called"


def derive_violations(trace: Iterable[Mapping[str, Any]]) -> tuple[set[str], dict[str, int], set[str]]:
    violations: set[str] = set()
    metrics = {
        "inbound_events": 0,
        "summaries": 0,
        "confirmations": 0,
        "commands": 0,
        "provider_dispatches": 0,
        "public_replies": 0,
        "handoffs": 0,
    }
    fault_points: set[str] = set()
    presented_versions: set[str] = set()
    accepted_versions: set[str] = set()
    dispatch_keys: set[str] = set()
    inbound_ids: set[str] = set()

    for item in trace:
        kind = str(item.get("kind") or "")
        if kind == "inbound":
            metrics["inbound_events"] += 1
            event_id = str(item.get("event_id") or "")
            if event_id in inbound_ids:
                violations.add("duplicate_webhook_not_deduplicated")
            inbound_ids.add(event_id)
        elif kind == "projection":
            required = set(item.get("required_fields") or [])
            dropped = set(item.get("dropped_fields") or [])
            if required.intersection(dropped):
                violations.add("authorization_state_projection_loss")
        elif kind == "summary_presented":
            metrics["summaries"] += 1
            version = str(item.get("draft_version") or "")
            if version in presented_versions:
                violations.add("same_draft_summary_presented_twice")
            presented_versions.add(version)
        elif kind == "confirmation_received":
            metrics["confirmations"] += 1
            version = str(item.get("draft_version") or "")
            if version not in presented_versions:
                violations.add("confirmation_without_presented_summary")
            if item.get("decision") == "accepted":
                accepted_versions.add(version)
        elif kind == "guard_blocked":
            version = str(item.get("draft_version") or "")
            reason = str(item.get("reason") or "")
            if reason == "confirmation_phase_required" and version in accepted_versions:
                violations.add("accepted_summary_requires_second_confirmation")
            if reason == "tool_turn_budget_exceeded" and item.get("after_guard_failure") is True:
                violations.add("guard_retry_consumes_turn_budget")
        elif kind == "phase_armed":
            version = str(item.get("draft_version") or "")
            if version not in presented_versions:
                violations.add("confirmation_phase_armed_before_summary")
        elif kind == "signature":
            if item.get("omitted_economic_fields"):
                violations.add("confirmation_signature_omits_economic_fields")
        elif kind == "lookup_use":
            age = int(item.get("age_seconds") or 0)
            ttl = int(item.get("ttl_seconds") or 0)
            if item.get("authorized") is True and age > ttl:
                violations.add("stale_lookup_authorized")
        elif kind == "selection":
            if item.get("selected") is True and item.get("identity_source") == "public_label":
                violations.add("public_label_controls_identity")
            if (
                item.get("technical_identity_equal") is True
                and item.get("public_labels_equal") is False
                and item.get("selected") is False
            ):
                violations.add("typographic_label_breaks_selection")
            if int(item.get("match_count") or 0) != 1 and item.get("selected") is True:
                violations.add("non_unique_selection_promoted")
        elif kind == "tool_surface":
            if item.get("legacy_alias") is True and item.get("guard_enforced") is False:
                violations.add("legacy_write_alias_bypasses_guard")
        elif kind == "concurrency":
            if int(item.get("decision_owners") or 0) > 1 or item.get("conflicting_commits") is True:
                violations.add("turn_has_multiple_unsynchronized_owners")
        elif kind == "redaction":
            if item.get("leaked_private_fields"):
                violations.add("nested_private_metadata_leak")
        elif kind == "provider_target":
            if item.get("decision_source") == "model" and item.get("state_determines_target") is True:
                violations.add("model_owns_provider_target")
        elif kind == "bootstrap":
            if item.get("discovery_source") in {"log", "filesystem"}:
                violations.add("session_bootstrap_is_implicit")
        elif kind == "runtime_attestation":
            if item.get("clean_commit") is False or item.get("mismatched_fields"):
                violations.add("runtime_artifact_provenance_drift")
        elif kind == "config":
            if item.get("dry_run_disables_real_reads") is True:
                violations.add("dry_run_couples_read_and_write_controls")
            agent = int(item.get("agent_timeout_seconds") or 0)
            provider = int(item.get("provider_timeout_seconds") or 0)
            ledger = int(item.get("ledger_seconds") or 0)
            margin = int(item.get("margin_seconds") or 0)
            if provider and agent < provider + ledger + margin:
                violations.add("write_budget_impossible_by_configuration")
        elif kind == "provider_contract":
            if item.get("fake_accepts") is True and item.get("provider_snapshot_accepts") is False:
                violations.add("fake_contract_accepts_provider_invalid_option")
        elif kind == "future_promise":
            if item.get("public_promise") is True and item.get("durable_continuation") is False:
                violations.add("future_promise_without_durable_workflow")
        elif kind == "command_created":
            metrics["commands"] += 1
            version = str(item.get("draft_version") or "")
            if version not in accepted_versions:
                violations.add("command_created_without_accepted_summary")
        elif kind == "dispatch":
            metrics["provider_dispatches"] += 1
            version = str(item.get("draft_version") or "")
            if version and version not in accepted_versions:
                violations.add("provider_dispatch_before_confirmation")
            key = str(item.get("idempotency_key") or "")
            if key and key in dispatch_keys:
                violations.add("duplicate_provider_dispatch")
            if key:
                dispatch_keys.add(key)
        elif kind == "composite_outcome":
            leaves = [str(value) for value in item.get("leaf_outcomes") or []]
            if str(item.get("parent_outcome") or "") != _aggregate_outcome(leaves):
                violations.add("composite_outcome_is_not_monotonic")
        elif kind == "handoff":
            metrics["handoffs"] += 1
            if (
                item.get("public_reply_sent") is False
                and item.get("tag_applied") is True
                and item.get("internal_email_required") is False
                and item.get("internal_email_sent") is False
            ):
                violations.add("optional_internal_email_blocks_public_handoff")
        elif kind == "readiness":
            if item.get("ready") is False and item.get("only_failed_check") == "local_capacity":
                violations.add("local_capacity_directly_flips_readiness")
        elif kind == "public_reply":
            metrics["public_replies"] += 1
        elif kind == "test_fixture":
            if item.get("historical_preseeded_fields"):
                violations.add("historical_test_preseeds_condition_under_test")
        elif kind == "crash":
            point = str(item.get("fault_point") or "")
            if point:
                fault_points.add(point)
            if point == "before_event_persist" and item.get("public_continuation_emitted") is True:
                violations.add("public_continuation_without_persisted_event")
            if (
                point == "after_event_before_command"
                and item.get("confirmation_event_persisted") is True
                and item.get("command_recoverable") is False
            ):
                violations.add("confirmed_event_has_no_recoverable_command")
            if point == "after_command_before_claim" and item.get("command_persisted") is False:
                violations.add("command_is_not_durable_before_claim")
            if (
                point == "after_claim_before_socket"
                and item.get("automatic_provider_retry") is True
                and item.get("claim_reconciled") is False
            ):
                violations.add("abandoned_claim_is_auto_retried")
            if point in {"after_socket_response_lost", "after_provider_before_outcome"}:
                if item.get("automatic_provider_retry") is True:
                    violations.add("uncertain_provider_call_is_auto_retried")
                if item.get("recorded_outcome") != "called_unknown":
                    violations.add("uncertain_provider_call_loses_certainty")
            if point in {"after_outcome_before_outbox", "during_public_delivery"}:
                if item.get("provider_repeated_for_message_recovery") is True:
                    violations.add("message_recovery_repeats_provider")

    return violations, metrics, fault_points


def replay_scenario(scenario: Mapping[str, Any], *, characterization_root: Path) -> ReplayResult:
    validate_scenario(scenario, characterization_root=characterization_root)
    violations, metrics, fault_points = derive_violations(scenario["trace"])
    expected = set(scenario["expected_violations"])
    if violations != expected:
        missing = sorted(expected - violations)
        unexpected = sorted(violations - expected)
        raise ScenarioValidationError(
            f"{scenario['case_id']}: violation mismatch; missing={missing}, unexpected={unexpected}"
        )
    return ReplayResult(
        incident_id=str(scenario["incident_id"]),
        case_id=str(scenario["case_id"]),
        classification=str(scenario["classification"]),
        violations=tuple(sorted(violations)),
        metrics=metrics,
        fault_points=tuple(sorted(fault_points)),
    )


def scenario_paths(characterization_root: Path) -> tuple[Path, ...]:
    incidents = characterization_root / "incidents"
    return tuple(sorted(incidents.glob("*.json")))


def load_and_replay_all(characterization_root: Path) -> tuple[ReplayResult, ...]:
    paths = scenario_paths(characterization_root)
    _require(bool(paths), "no incident scenarios found")
    return tuple(
        replay_scenario(_load_json(path), characterization_root=characterization_root)
        for path in paths
    )


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        results = load_and_replay_all(root)
    except ScenarioValidationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "status": "ok",
        "scenario_count": len(results),
        "incident_ids": sorted({result.incident_id for result in results}),
        "classifications": {
            classification: sum(result.classification == classification for result in results)
            for classification in sorted(CLASSIFICATIONS)
        },
        "fault_points": sorted({point for result in results for point in result.fault_points}),
        "violation_count": sum(len(result.violations) for result in results),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
