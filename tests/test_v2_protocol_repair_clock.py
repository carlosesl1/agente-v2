"""A protocol repair preserves the original request snapshot across clock boundaries."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import v2_adapters.hermes_model as model_module
from v2_adapters.hermes_model import HermesModelAdapter
from v2_contracts.model import ModelRequest
from tests.test_v2_hermes_model_adapter import (
    _v8_bytes,
    test_normal_protocol_rejects_each_legacy_schema_and_repairs_with_v8 as assert_repaired_payload_unchanged,
)


def advancing_clock(monkeypatch, initial):
    calls = []

    class AdvancingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = initial + timedelta(seconds=len(calls))
            calls.append(instant)
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(model_module, "datetime", AdvancingClock)
    return calls


@pytest.mark.parametrize("legacy_version", range(1, 7))
@pytest.mark.parametrize("initial", (
    datetime(2026, 9, 24, 21, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 25, 2, 59, 59, tzinfo=timezone.utc),  # Bahia midnight boundary
))
def test_protocol_repair_reuses_original_snapshot_as_clock_advances(monkeypatch, initial, legacy_version):
    calls = advancing_clock(monkeypatch, initial)
    # Keeps the existing full payload, frame, protocol and authorship assertions.
    assert_repaired_payload_unchanged(legacy_version)
    assert len(calls) == 1


def test_separate_model_turns_get_fresh_clocks(monkeypatch):
    calls = advancing_clock(monkeypatch, datetime(2026, 9, 24, 21, 0, 0, tzinfo=timezone.utc))
    clocks = []

    def run(command, **kwargs):
        envelope = json.loads(kwargs["input"])
        clocks.append(json.loads(envelope["messages"][-1][1])["business_clock"])
        return SimpleNamespace(returncode=0, stdout=b"PHASE8_RESULT\x00" + _v8_bytes(), stderr=b"")

    adapter = HermesModelAdapter(command=("synthetic-tool-free-child",), system_prompt="closed prompt",
                                 timeout=10, transcript_key=b"clock-snapshot-transcript-key-001",
                                 run=run, environ={})
    for index in range(2):
        adapter.complete_audited(ModelRequest(request_id=f"request:clock-{index}", lead_id="manychat:clock",
                                            source_event_id=f"event:clock-{index}", message="Olá",
                                            locale="pt-BR", state_version=index))
    assert len(calls) == 2
    assert clocks[0] != clocks[1]
