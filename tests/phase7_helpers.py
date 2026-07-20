"""Synthetic Phase 7 test helpers with no runtime/provider dependency."""

from __future__ import annotations

from datetime import datetime, timezone


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 7, 20, 12, 2, tzinfo=timezone.utc)


def raw_legacy_fields() -> dict[str, object]:
    return {
        "lead_key": "lead-synthetic-001",
        "stage": "hostel",
        "metadata": {
            "selected_offer_id": "offer-001",
            "guests": ["adult", "child"],
        },
    }
