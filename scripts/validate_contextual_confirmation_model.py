#!/usr/bin/env python3
"""Tool-free semantic gate for the narrow contextual-confirmation reviewer."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Final

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v2_adapters.hermes_model import HermesModelAdapter
from v2_contracts.critical_actions import (
    ApprovalBasis,
    CriticalActionKind,
    PendingCriticalActionContext,
)
from v2_contracts.model import InvalidModelProposal, ModelRequest

_PT_SUMMARY: Final = (
    "Resumo pendente: reservar uma Suíte Casal de 14/09/2026 a 15/09/2026 "
    "para 2 adultos e 1 criança, total BRL 450,00, e depois enviar as "
    "instruções Pix correspondentes. Posso executar exatamente essas ações?"
)
_EN_SUMMARY: Final = (
    "Pending summary: reserve one Double Suite from 14 September 2026 to "
    "15 September 2026 for 2 adults and 1 child, total BRL 450.00, then send "
    "the corresponding Pix instructions. May I execute exactly those actions?"
)
_ACTIONS: Final = (
    CriticalActionKind.INITIATE_PAYMENT,
    CriticalActionKind.RESERVE_LODGING,
)


@dataclass(frozen=True, slots=True)
class ValidationCase:
    label: str
    locale: str
    message: str
    summary: str
    expected_intent: str


CASES: Final = (
    ValidationCase(
        "canary_witness",
        "pt-BR",
        "Sim, confirmo exatamente esse resumo. Pode fazer a reserva agora.",
        _PT_SUMMARY,
        "confirm",
    ),
    ValidationCase(
        "free_paraphrase",
        "pt-BR",
        "O conteúdo integral acima corresponde ao que decidi; prossiga com o "
        "conjunto completo tal como foi apresentado, sem modificar nada.",
        _PT_SUMMARY,
        "confirm",
    ),
    ValidationCase(
        "english_paraphrase",
        "en-US",
        "Everything in that summary matches my decision in full; carry out the "
        "whole set exactly as presented, with no changes.",
        _EN_SUMMARY,
        "confirm",
    ),
    ValidationCase(
        "question",
        "pt-BR",
        "Se eu aceitar esse resumo, quando recebo as instruções de pagamento?",
        _PT_SUMMARY,
        "inform",
    ),
    ValidationCase(
        "hesitation",
        "en-US",
        "I might go ahead with that, but I am still thinking about it.",
        _EN_SUMMARY,
        "inform",
    ),
    ValidationCase(
        "refusal",
        "pt-BR",
        "Não prossiga com o resumo; desisti por enquanto.",
        _PT_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "postponement",
        "en-US",
        "Leave that summary pending until tomorrow; do not carry it out now.",
        _EN_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "condition",
        "pt-BR",
        "Faça o que está no resumo somente se houver café incluído.",
        _PT_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "date_change",
        "pt-BR",
        "Pode seguir, mas altere a saída para 16 de setembro.",
        _PT_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "party_change",
        "pt-BR",
        "Prossiga com a reserva para apenas dois adultos, sem a criança.",
        _PT_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "amount_change",
        "en-US",
        "Go ahead only if the final total is BRL 400.00 instead.",
        _EN_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "payment_change",
        "pt-BR",
        "A reserva está certa, porém troque o Pix por cartão.",
        _PT_SUMMARY,
        "adjust",
    ),
    ValidationCase(
        "scope_narrowing",
        "pt-BR",
        "Reserve o quarto agora, mas não envie as instruções Pix.",
        _PT_SUMMARY,
        "adjust",
    ),
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _request(case: ValidationCase) -> ModelRequest:
    return ModelRequest(
        request_id=f"request:semantic-gate:{case.label}",
        lead_id=f"synthetic:semantic-gate:{case.label}",
        source_event_id=f"batch:semantic-gate:{case.label}",
        message=case.message,
        locale=case.locale,
        state_version=0,
        pending_action=PendingCriticalActionContext(
            summary_version=1,
            action_kinds=_ACTIONS,
            public_summary=case.summary,
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        ),
        confirmation_review_required=True,
    )


def _proposal_matches(
    request: ModelRequest,
    expected_intent: str,
    proposal,
) -> bool:
    pending = request.pending_action
    if pending is None or proposal.intent != expected_intent:
        return False
    if expected_intent == "confirm":
        return (
            proposal.confirmed_summary_version == pending.summary_version
            and proposal.confirmed_action_kinds == pending.action_kinds
            and proposal.approval_basis is ApprovalBasis.CONTEXTUAL_REFERENCE
            and proposal.facts == ()
            and proposal.read_requests == ()
            and proposal.effect_proposals == ()
            and proposal.passengers == ()
        )
    if expected_intent == "adjust":
        return (
            proposal.pending_disposition == "revoke"
            and proposal.confirmed_summary_version is None
            and proposal.confirmed_action_kinds == ()
            and proposal.approval_basis is None
        )
    return (
        expected_intent == "inform"
        and proposal.confirmed_summary_version is None
        and proposal.confirmed_action_kinds == ()
        and proposal.approval_basis is None
    )


def run_validation(adapter: HermesModelAdapter) -> dict[str, object]:
    suite_rows = []
    result_rows = []
    passed = 0
    for case in CASES:
        request = _request(case)
        actual = "invalid"
        proposal_valid = False
        try:
            turn = adapter.complete_audited(request)
            actual = turn.proposal.intent
            proposal_valid = _proposal_matches(
                request,
                case.expected_intent,
                turn.proposal,
            )
        except (InvalidModelProposal, TypeError, ValueError):
            proposal_valid = False
        ok = actual == case.expected_intent and proposal_valid
        passed += int(ok)
        suite_rows.append(
            {
                "label_hash": hashlib.sha256(case.label.encode()).hexdigest(),
                "locale": case.locale,
                "message_hash": hashlib.sha256(case.message.encode()).hexdigest(),
                "summary_hash": hashlib.sha256(case.summary.encode()).hexdigest(),
                "expected": case.expected_intent,
            }
        )
        result_rows.append(
            {
                "label_hash": hashlib.sha256(case.label.encode()).hexdigest(),
                "actual": actual,
                "proposal_valid": proposal_valid,
                "passed": ok,
            }
        )
    return {
        "schema": "v2-contextual-confirmation-validation-v1",
        "case_count": len(CASES),
        "passed": passed,
        "failed": len(CASES) - passed,
        "suite_hash": hashlib.sha256(_canonical(suite_rows)).hexdigest(),
        "result_hash": hashlib.sha256(_canonical(result_rows)).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command-json", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        decoded = json.loads(args.command_json)
        if (
            type(decoded) is not list
            or not decoded
            or any(type(item) is not str or not item or "\x00" in item for item in decoded)
        ):
            raise ValueError("command must be a non-empty exact string array")
        adapter = HermesModelAdapter(
            command=tuple(decoded),
            system_prompt="unused by the narrow confirmation reviewer",
            timeout=args.timeout,
            transcript_key=hashlib.sha256(
                b"v2-contextual-confirmation-sandbox-validation-v1"
            ).digest(),
        )
        report = run_validation(adapter)
    except (json.JSONDecodeError, TypeError, ValueError):
        print('{"schema":"v2-contextual-confirmation-validation-error-v1"}')
        return 2
    print(_canonical(report).decode())
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
