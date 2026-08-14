from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from v2_contracts.providers import InvalidReadRequest, ReadKind, ReadRequest


REQUEST = ReadRequest(
    request_id="read:recommendation:one",
    kind=ReadKind.ACTIVITY_RECOMMENDATION,
    period_start=date(2026, 9, 10),
    period_end=date(2026, 9, 15),
    adults=2,
    children=0,
    locale="pt-BR",
)


def test_recommendation_request_is_closed_and_canonical() -> None:
    assert json.loads(REQUEST.to_canonical_bytes()) == {
        "adults": 2,
        "children": 0,
        "kind": "activity_recommendation",
        "locale": "pt-BR",
        "period_end": "2026-09-15",
        "period_start": "2026-09-10",
        "request_id": "read:recommendation:one",
    }
    assert (
        replace(REQUEST, request_id="read:recommendation:two").query_hash()
        == REQUEST.query_hash()
    )
    assert replace(REQUEST, adults=3).query_hash() != REQUEST.query_hash()
    assert (
        replace(REQUEST, period_end=date(2026, 9, 16)).query_hash()
        != REQUEST.query_hash()
    )


@pytest.mark.parametrize(
    "period_end",
    [date(2026, 9, 10), date(2026, 9, 23)],
)
def test_recommendation_request_accepts_inclusive_period_boundaries(
    period_end: date,
) -> None:
    assert replace(REQUEST, period_end=period_end).period_end == period_end


@pytest.mark.parametrize(
    "changes",
    [
        {"period_start": None},
        {"period_end": None},
        {"period_end": date(2026, 9, 9)},
        {"period_end": date(2026, 9, 24)},
        {"adults": 0},
        {"adults": True},
        {"children": -1},
        {"children": False},
        {"product_id": "product:tour-4ps"},
        {"activity_date": date(2026, 9, 10)},
        {"participants": 2},
        {"offer_id": "offer:tour-4ps"},
        {"check_in": date(2026, 9, 10)},
        {"check_out": date(2026, 9, 15)},
        {"query": "passeio tranquilo"},
    ],
)
def test_recommendation_request_rejects_open_or_invalid_shapes(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidReadRequest):
        replace(REQUEST, **changes)
