from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from v2_application.turn_plan import (
    derive_selection_reads,
)
from v2_contracts.model import ConsultationHistoryEntry, ModelFact, ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _proposal(
    *,
    facts: tuple[ModelFact, ...],
    reads: tuple[ReadRequest, ...] = (),
    intent: str = "inform",
) -> ModelProposal:
    return ModelProposal(
        source_event_id="batch:turn-plan",
        intent=intent,
        reply_chunks=("Vou verificar.",),
        facts=facts,
        read_requests=reads,
        effect_proposals=(),
        target_offer_ids=(
            ("offer:" + "1" * 64, "offer:" + "2" * 64)
            if intent == "select"
            else ()
        ),
    )


def _lodging_read(*, check_out: date = date(2026, 9, 12)) -> ReadRequest:
    return ReadRequest(
        request_id="read:turn-plan",
        kind=ReadKind.LODGING,
        check_in=date(2026, 9, 10),
        check_out=check_out,
        adults=2,
        children=0,
    )


def _history(*, fresh: bool = True, check_out: str = "2026-09-12") -> ConsultationHistoryEntry:
    return ConsultationHistoryEntry(
        observation_hash="a" * 64,
        observed_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        fresh_at_turn_start=fresh,
        public_context={
            "service": "lodging",
            "status": "positive",
            "query": {
                "check_in": "2026-09-10",
                "check_out": check_out,
                "adults": 2,
                "children": 0,
            },
            "offers": [
                {
                    "public_label": "Quarto privativo",
                    "start_date": "2026-09-10",
                    "end_date": check_out,
                    "start_time": None,
                    "adults": 2,
                    "children": 0,
                    "total_amount": "580.00",
                    "currency": "BRL",
                }
            ],
            "offer_count": 1,
            "offers_truncated": False,
        },
    )


def test_complete_package_selection_derives_both_refresh_components() -> None:
    proposal = _proposal(
        intent="select",
        facts=(
            ModelFact("service", "package"),
            ModelFact("start_date", date(2026, 9, 10)),
            ModelFact("end_date", date(2026, 9, 12)),
            ModelFact("product_id", "product:buracao"),
            ModelFact("activity_date", date(2026, 9, 10)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
        )
    )

    normalized = derive_selection_reads(proposal)

    assert tuple(item.kind for item in normalized) == (
        ReadKind.LODGING,
        ReadKind.ACTIVITY,
    )
    assert proposal.intent == "select"


def test_incomplete_inform_plan_does_not_invent_a_read() -> None:
    proposal = _proposal(
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("adults", 2),
            ModelFact("children", 0),
        )
    )

    assert derive_selection_reads(proposal) == ()


def test_complete_inform_plan_without_model_read_stays_read_free() -> None:
    proposal = _proposal(
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 9, 10)),
            ModelFact("end_date", date(2026, 9, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
        )
    )

    assert derive_selection_reads(proposal) == ()


def test_incomplete_selection_produces_no_refresh_and_keeps_typed_intent() -> None:
    proposal = ModelProposal(
        source_event_id="batch:turn-plan-incomplete-selection",
        intent="select",
        reply_chunks=(
            "Maya sentinel: preciso confirmar os dados que ainda faltam.",
            "Quais datas você prefere?",
        ),
        clarification_question="Quais datas você prefere?",
        facts=(ModelFact("service", "hostel"),),
        read_requests=(),
        effect_proposals=(),
        target_offer_id="offer:" + "3" * 64,
    )

    normalized = derive_selection_reads(proposal)

    assert normalized == ()
    assert proposal.intent == "select"
    assert proposal.target_offer_id == "offer:" + "3" * 64


def test_explicit_reads_remain_model_owned() -> None:
    request = _lodging_read()
    proposal = _proposal(facts=(), reads=(request,))
    assert derive_selection_reads(proposal) == ()
    assert derive_selection_reads(_proposal(facts=())) == ()
