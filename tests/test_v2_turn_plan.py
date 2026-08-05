from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from v2_application.turn_plan import (
    normalize_initial_commercial_plan,
    reuses_fresh_consultation,
)
from v2_contracts.model import ConsultationHistoryEntry, ModelFact, ModelProposal
from v2_contracts.providers import ReadKind, ReadRequest

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _proposal(*, facts: tuple[ModelFact, ...], reads: tuple[ReadRequest, ...] = ()) -> ModelProposal:
    return ModelProposal(
        source_event_id="batch:turn-plan",
        intent="inform",
        reply_chunks=("Vou verificar.",),
        facts=facts,
        read_requests=reads,
        effect_proposals=(),
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


def test_complete_package_plan_derives_both_read_only_components() -> None:
    proposal = _proposal(
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

    normalized = normalize_initial_commercial_plan(proposal)

    assert tuple(item.kind for item in normalized.read_requests) == (
        ReadKind.LODGING,
        ReadKind.ACTIVITY,
    )
    assert normalized.effect_proposals == ()
    assert normalized.intent == "inform"


def test_incomplete_inform_plan_does_not_invent_a_read() -> None:
    proposal = _proposal(
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("adults", 2),
            ModelFact("children", 0),
        )
    )

    assert normalize_initial_commercial_plan(proposal) == proposal


def test_complete_inform_plan_respects_explicit_read_deferral() -> None:
    proposal = _proposal(
        facts=(
            ModelFact("service", "hostel"),
            ModelFact("start_date", date(2026, 9, 10)),
            ModelFact("end_date", date(2026, 9, 12)),
            ModelFact("adults", 2),
            ModelFact("children", 0),
        )
    )

    assert (
        normalize_initial_commercial_plan(
            proposal,
            informational_read_requested=False,
        )
        == proposal
    )


def test_consultation_reuse_requires_exact_fresh_scope_and_recap_only_plan() -> None:
    request = _lodging_read()
    proposal = _proposal(facts=(), reads=(request,))

    assert reuses_fresh_consultation(proposal, (_history(),), now=NOW) is True
    assert (
        reuses_fresh_consultation(proposal, (_history(fresh=False),), now=NOW)
        is False
    )
    assert (
        reuses_fresh_consultation(
            proposal,
            (replace(_history(), expires_at=NOW + timedelta(minutes=1)),),
            now=NOW + timedelta(minutes=1),
        )
        is False
    )
    assert reuses_fresh_consultation(
        proposal,
        (_history(check_out="2026-09-13"),),
        now=NOW,
    ) is False
    assert reuses_fresh_consultation(
        replace(proposal, facts=(ModelFact("payment_method", "pix"),)),
        (_history(),),
        now=NOW,
    ) is False
    assert reuses_fresh_consultation(
        replace(proposal, selection_requested=True),
        (_history(),),
        now=NOW,
    ) is False
