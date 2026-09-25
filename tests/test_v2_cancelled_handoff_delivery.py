"""An operator-closed incident must never re-pause ManyChat from old outbox work."""
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from reservation_followup.handoff import HandoffCancelled, HandoffCancellationCode
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork, StaleLease
from reservation_followup.workers import HandoffOutboxWorker
from tests.phase6_helpers import handoff_requested
from tests.test_v2_manychat_handoff_delivery import _store, _adapter, _transport, NOW


def cancel(store):
    request = handoff_requested()
    event = HandoffCancelled(
        request.handoff_id, request.incident_key,
        HandoffCancellationCode.OPERATOR_CANCELLED, NOW + timedelta(seconds=1),
    )
    assert store.apply_handoff(request.handoff_id, 1, event).status.value == "applied"
    assert store.apply_handoff(request.handoff_id, 1, event).status.value == "noop"


def claim(store, at=NOW):
    return store.claim_handoff_outbox(worker_id="cancel-test", delivery_id="delivery:test",
        delivery_version=1, now=at, lease_ttl=timedelta(seconds=30))


@pytest.mark.parametrize("leased_before_cancel", [False, True])
def test_closed_handoff_is_not_claimed_even_after_restart(tmp_path, leased_before_cancel):
    store = _store(tmp_path)
    if leased_before_cancel:
        assert claim(store) is not None
    cancel(store)
    before = store._connection.execute("SELECT * FROM handoff_outbox").fetchall()
    store.close()
    store = SQLiteFollowupUnitOfWork.open_v2(tmp_path / "followup.sqlite3")
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, request=request, json={"status": "success"})
    from types import SimpleNamespace
    at = NOW + timedelta(minutes=1)
    delivery = _adapter(_transport(handler))
    delivery._clock = SimpleNamespace(now=lambda: at)
    worker = HandoffOutboxWorker(store=store, delivery=delivery,
        worker_id="cancel-worker", lease_ttl=timedelta(seconds=30), clock=lambda: at)
    assert worker.run_once(now=at).disposition.value == "idle"
    assert not seen
    assert store._connection.execute("SELECT * FROM handoff_outbox").fetchall() == before
    assert not store.load_handoff(handoff_requested().handoff_id).queue_active
    store.close()


def test_cancel_between_claim_and_dispatch_keeps_slot_unconsumed(tmp_path):
    store = _store(tmp_path)
    lease = claim(store)
    cancel(store)
    with pytest.raises(StaleLease, match="cancelled"):
        store.begin_handoff_delivery(lease, now=NOW + timedelta(seconds=2))
    assert store._connection.execute("SELECT dispatch_slots_consumed FROM handoff_outbox").fetchone() == (0,)
    store.close()


def test_cancelled_pending_handoff_does_not_starve_another_incident(tmp_path):
    from reservation_followup import HandoffEffectPolicy
    store = _store(tmp_path)
    cancel(store)
    second = replace(handoff_requested(), handoff_id="handoff:second", incident_key="incident:second",
        source_event_id="event:second", requested_at=NOW + timedelta(seconds=2))
    store.open_handoff(second, HandoffEffectPolicy.default_email_disabled())
    lease = claim(store, NOW + timedelta(seconds=3))
    assert lease is not None and lease.message.handoff_id == second.handoff_id
    store.begin_handoff_delivery(lease, now=NOW + timedelta(seconds=4))
    assert store._connection.execute("SELECT dispatch_slots_consumed FROM handoff_outbox WHERE handoff_id=?", (second.handoff_id,)).fetchone() == (1,)
    store.close()
