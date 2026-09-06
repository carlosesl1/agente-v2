"""B1: real worker loop, blocked work, file heartbeat, thread-bound SQLite."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import Event, Thread, get_ident
import time
from types import SimpleNamespace

import pytest

from v2_host import composition, settings, worker_main
from v2_host.composition import _validate_worker_heartbeat_payload
from v2_host.worker_main import SQLiteWorkerOwnershipLease, WorkerCycle, WorkerQueue

_REAL_CONTAINER = composition.V2Container


def eventually(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        Event().wait(0.01)
    raise AssertionError("worker did not make the expected bounded progress")


@pytest.fixture
def running_worker(tmp_path: Path, monkeypatch):
    entered = Event()
    release = Event()
    closed = Event()
    failures = []
    completed = {}
    calls = {queue: 0 for queue in WorkerQueue}
    offset = [0.0]
    owner = []
    path = tmp_path / "v2-worker-heartbeat.json"
    config = SimpleNamespace(
        sqlite_path=tmp_path / "state.sqlite3",
        worker_heartbeat_path=path,
        worker_heartbeat_max_age_seconds=1,
        hermes_timeout_seconds=1,
    )

    class Container:
        @classmethod
        def open(cls, **kwargs):
            instance = cls()
            owner.append(get_ident())
            instance.db = sqlite3.connect(config.sqlite_path)
            return instance

        def assert_owner(self):
            assert get_ident() == owner[0]
            self.db.execute("SELECT 1").fetchone()

        def controlled_public_ingress_reason(self, *, now):
            self.assert_owner()
            return None

        def public_turn_capacity(self, *, now):
            self.assert_owner()
            return 3

        def close(self):
            self.assert_owner()
            self.db.close()
            closed.set()

    class Runner:
        def __init__(self, queue, container):
            self.queue = queue
            self.container = container

        def run_once(self, *, now):
            self.container.assert_owner()
            calls[self.queue] += 1
            if self.queue is WorkerQueue.INBOX and calls[self.queue] == 2:
                entered.set()
                assert release.wait(8), "test must release blocked worker"
                raise KeyboardInterrupt
            # Observable elapsed time between claim and queue completion.
            Event().wait(0.005)
            completed[self.queue] = datetime.now(timezone.utc)
            return None

    monkeypatch.setattr(settings.V2Settings, "from_env", lambda **kwargs: config)
    monkeypatch.setattr(composition, "V2Container", Container)
    monkeypatch.setattr(worker_main, "_load_worker_factory", lambda _: (
        lambda *, container, settings: {
            queue: Runner(queue, container) for queue in WorkerQueue
        }
    ))
    monkeypatch.setattr(worker_main, "build_worker_cycle", lambda _, workers: WorkerCycle(workers))
    real_monotonic = time.monotonic
    monkeypatch.setattr(worker_main, "time", SimpleNamespace(
        monotonic=lambda: real_monotonic() + offset[0],
        sleep=lambda interval: Event().wait(interval),
    ))
    monkeypatch.setenv("V2_WORKER_INTERVAL_SECONDS", "0.01")

    def run():
        try:
            worker_main.main()
        except KeyboardInterrupt:
            pass
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=run, name="test-worker-owner")
    thread.start()
    try:
        assert entered.wait(3), failures
        assert path.exists()
        yield SimpleNamespace(
            path=path, read=lambda: json.loads(path.read_text()),
            calls=calls, completed=completed, offset=offset,
            release=release, thread=thread, closed=closed, failures=failures,
            lock=tmp_path / "v2-worker-owner.lock",
        )
    finally:
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert not failures
        assert closed.is_set()


def test_busy_worker_refreshes_heartbeat_without_fabricating_queue_completion(running_worker):
    worker = running_worker
    first = worker.read()
    later = eventually(lambda: (
        value if (value := worker.read())["observed_at"] > first["observed_at"] else None
    ))
    _validate_worker_heartbeat_payload(later)
    assert later["public_ingress_ready"] is True
    assert later["public_turn_capacity"] == 3
    assert later["queues"] == first["queues"]
    assert worker.calls[WorkerQueue.INBOX] == 2
    assert all(worker.calls[queue] == 1 for queue in WorkerQueue if queue is not WorkerQueue.INBOX)


def test_worker_records_actual_queue_and_heartbeat_completion_times(running_worker):
    worker = running_worker
    payload = worker.read()
    for queue in WorkerQueue:
        success = datetime.fromisoformat(payload["queues"][queue.value]["last_success_at"])
        assert success >= worker.completed[queue]
    assert datetime.fromisoformat(payload["observed_at"]) >= max(worker.completed.values())


def test_stuck_worker_cannot_refresh_heartbeat_indefinitely(running_worker):
    worker = running_worker
    # Advance only monotonic time beyond every configured work budget; no clock
    # change can be mistaken for successful queue progress.
    worker.offset[0] += 10_000
    Event().wait(0.5)
    expired = worker.path.read_bytes()
    Event().wait(0.5)
    assert worker.path.read_bytes() == expired
    assert worker.thread.is_alive()
    assert worker.calls[WorkerQueue.INBOX] == 2


def test_heartbeat_stops_before_worker_releases_sqlite_ownership(running_worker):
    worker = running_worker
    worker.release.set()
    worker.thread.join(3)
    assert not worker.thread.is_alive()
    assert worker.closed.is_set()
    stopped = worker.path.read_bytes()
    replacement = SQLiteWorkerOwnershipLease(worker.lock).acquire()
    try:
        Event().wait(0.5)
        assert worker.path.read_bytes() == stopped
    finally:
        replacement.close()


def test_real_api_remains_ready_and_persists_new_lead_while_worker_is_busy(
    running_worker, monkeypatch,
):
    from fastapi.testclient import TestClient

    worker = running_worker
    # The synthetic worker's graph remains on its owner thread. The API opens
    # its own real SQLite connection, exactly as a separate process would.
    monkeypatch.setattr(composition, "V2Container", _REAL_CONTAINER)
    from v2_host.api_main import build_api_app
    api_settings = settings.V2Settings(
        webhook_secret="synthetic-worker-responsiveness-secret",
        sqlite_path=worker.path.with_name("api-inbox.sqlite3"),
        require_worker_heartbeat=True,
        worker_heartbeat_max_age_seconds=1,
    )
    with TestClient(build_api_app(api_settings)) as client:
        Event().wait(1.1)  # Exceed freshness of the last completed worker cycle.
        assert client.get("/readyz").status_code == 200
        response = client.post(
            "/webhook/manychat",
            headers={"X-V2-Webhook-Secret": "synthetic-worker-responsiveness-secret"},
            json={
                "message_id": "synthetic-worker-event-2",
                "subscriber_id": "123456789",
                "message": "Mensagem sintética",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert response.status_code == 202
        assert client.app.state.v2_container.inbox.pending_count() == 1
        assert worker.calls[WorkerQueue.INBOX] == 2

        # An alive heartbeat thread is not sufficient: without owner progress
        # the deadline expires and real API health must become stale.
        worker.offset[0] += 10_000
        Event().wait(1.5)
        unhealthy = client.get("/readyz")
        assert unhealthy.status_code == 503
        assert "worker_heartbeat_stale" in unhealthy.json()["reasons"]
