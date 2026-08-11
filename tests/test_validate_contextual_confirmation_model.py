from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.validate_contextual_confirmation_model import CASES, run_validation
from v2_adapters.hermes_model import (
    _CONFIRMATION_REVIEW_SYSTEM_PROMPT,
    _confirmation_proposal,
    HermesModelAdapter,
)


def test_parent_binding_never_classifies_message_text_or_uses_lexical_triggers() -> None:
    source = inspect.getsource(_confirmation_proposal)
    prompt = " ".join(_CONFIRMATION_REVIEW_SYSTEM_PROMPT.split())

    assert "request.message" not in source
    assert "casefold" not in source
    assert "re." not in source
    assert "Sim, confirmo exatamente esse resumo" not in prompt
    assert "Confirmed. Please book exactly that summary" not in prompt
    assert "Judge the complete message semantically in context" in prompt
    assert "never decide from a word, token, emoji" in prompt


def test_sandbox_validator_checks_typed_semantics_without_printing_messages() -> None:
    expected = {case.message: case.expected_intent for case in CASES}
    captured_inputs: list[bytes] = []

    def run(command, **kwargs):
        assert command == ("synthetic-tool-free-child",)
        wire = kwargs["input"]
        captured_inputs.append(wire)
        envelope = json.loads(wire)
        user = json.loads(envelope["messages"][0][1])
        intent = expected[user["message"]]
        response = json.dumps(
            {
                "intent": intent,
                "reply_chunks": [
                    {
                        "text": "Synthetic contextual review result.",
                        "expects_reply": False,
                    }
                ],
                "facts": [],
                "read_requests": [],
                "selected_choice_refs": [],
                "selection_requested": False,
                "pending_action_disposition": (
                    "revoke" if intent == "adjust" else None
                ),
                "passengers": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return SimpleNamespace(
            returncode=0,
            stdout=b"PHASE8_RESULT\x00" + response,
            stderr=b"",
        )

    adapter = HermesModelAdapter(
        command=("synthetic-tool-free-child",),
        system_prompt="unused-general-prompt",
        timeout=10,
        transcript_key=b"synthetic-review-validation-key-001",
        run=run,
        environ={},
    )

    report = run_validation(adapter)

    assert report["case_count"] == len(CASES)
    assert report["passed"] == len(CASES)
    assert report["failed"] == 0
    assert len(report["suite_hash"]) == 64
    assert len(report["result_hash"]) == 64
    assert len(captured_inputs) == len(CASES)
    serialized_report = json.dumps(report, sort_keys=True)
    for case in CASES:
        assert case.message not in serialized_report
        assert case.summary not in serialized_report
    for wire in captured_inputs:
        serialized = wire.decode()
        assert "provider_ref" not in serialized
        assert "subject_signature" not in serialized
        assert "private_customer" not in serialized
        assert "offer:" not in serialized


def test_sandbox_validator_case_matrix_covers_approval_and_false_positive_classes() -> None:
    decisions = {case.expected_intent for case in CASES}
    locales = {case.locale for case in CASES}
    labels = {case.label for case in CASES}

    assert decisions == {"confirm", "adjust", "inform"}
    assert locales == {"pt-BR", "en-US"}
    assert {
        "canary_witness",
        "free_paraphrase",
        "question",
        "hesitation",
        "refusal",
        "postponement",
        "condition",
        "date_change",
        "party_change",
        "amount_change",
        "payment_change",
        "scope_narrowing",
    } <= labels


def test_sandbox_validator_has_no_effect_capability_imports() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "validate_contextual_confirmation_model.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    forbidden_prefixes = (
        "sqlite3",
        "reservation_execution",
        "reservation_followup",
        "v2_adapters.cloudbeds",
        "v2_adapters.bokun",
        "v2_application.payments",
        "v2_application.public_delivery",
        "v2_application.relay_worker",
        "v2_application.reservations",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported
        for prefix in forbidden_prefixes
    )
