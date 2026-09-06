"""Rollback compatibility against immutable predecessor/candidate writers.

Only synthetic local databases and ScriptedDelivery; no deployed worktree imports.
Git objects are required because the writer must be the actual release, not a
hand-written approximation of its schema or persisted dispatch state.
"""
from datetime import timedelta
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from reservation_followup.workers import HandoffOutboxWorker
from tests.phase6_helpers import T0
from tests.test_phase6_handoff_worker import ScriptedDelivery
from v2_host.composition import V2Container, V2Role
from v2_host.settings import V2Settings

BASE = "b3173693d6d852ba8bcf8df5f7aa8e7a6d4c10f3"
CANDIDATE = "0d790e7c8ce842a37abd5baab1035c1b65f774fd"
ROOT = Path(__file__).resolve().parents[1]
NOW = T0 + timedelta(minutes=1)

WRITER = '''
import json, sys
from datetime import timedelta
from reservation_followup import HandoffEffectPolicy
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from reservation_followup.workers import HandoffOutboxWorker, HandoffDeliveryUnknown
from tests.phase6_helpers import T0, handoff_requested
from tests.test_phase6_handoff_worker import ScriptedDelivery
path, version, action = sys.argv[1:]
now = T0 + timedelta(minutes=1)
store = (SQLiteFollowupUnitOfWork.open(path) if version == "1" else
         SQLiteFollowupUnitOfWork.open_v2(path, migrate_v1=True))
try:
    if action != "inspect":
        store.open_handoff(handoff_requested(), HandoffEffectPolicy.default_email_disabled())
        if action in ("delivered", "manual_review", "consumed"):
            result = now if action == "delivered" else (
                HandoffDeliveryUnknown("synthetic") if action == "manual_review" else KeyboardInterrupt())
            worker = HandoffOutboxWorker(store=store, delivery=ScriptedDelivery([result]),
                worker_id="immutable-writer", lease_ttl=timedelta(seconds=30))
            try:
                worker.run_once(now=now)
            except KeyboardInterrupt:
                assert action == "consumed"
        elif action == "leased":
            store.claim_handoff_outbox(worker_id="legacy-writer", now=now,
                lease_ttl=timedelta(seconds=30), delivery_id=ScriptedDelivery.delivery_id,
                delivery_version=1)
    tables = [name for name, _ in store._table_rows()]
    print(json.dumps({name: {
        "columns": [r[1] for r in store._connection.execute("PRAGMA table_info("+name+")")],
        "rows": store._connection.execute("SELECT * FROM "+name+" ORDER BY 1").fetchall()
    } for name in tables}))
finally:
    store.close()
'''


@pytest.fixture
def immutable_writer(tmp_path):
    def run(revision, path, action):
        export = tmp_path / revision
        if not export.exists():
            export.mkdir()
            archive = subprocess.check_output(
                ["git", "archive", revision, "reservation_followup"], cwd=ROOT
            )
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(export, filter="data")
        bootstrap = (
            "import sys; sys.path[:0]=" + repr([str(export), str(ROOT)]) + ";\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", bootstrap + WRITER, str(path),
             "1" if revision == BASE else "2", action],
            cwd=export, capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)
    return run


def assert_preserved(store, snapshot):
    for name, before in snapshot.items():
        rows = store._connection.execute(
            "SELECT " + ",".join(before["columns"]) + " FROM " + name + " ORDER BY 1"
        ).fetchall()
        assert [list(row) for row in rows] == before["rows"], name
    assert store._connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert not store._connection.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("revision,state", [
    (BASE, "pending"), (BASE, "delivered"), (BASE, "leased"),
    (CANDIDATE, "pending"), (CANDIDATE, "delivered"),
    (CANDIDATE, "consumed"), (CANDIDATE, "manual_review"),
])
def test_host_opens_release_writes_preserves_rows_and_never_resends_consumed(
    tmp_path, immutable_writer, revision, state
):
    path = tmp_path / "v2-followup.sqlite3"
    before = immutable_writer(revision, path, state)
    settings = V2Settings(webhook_secret="synthetic", sqlite_path=tmp_path / "inbox.sqlite3")
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    late = NOW + timedelta(seconds=31)
    delivery = ScriptedDelivery([late])
    try:
        assert container.followup._schema_version == 2
        assert_preserved(container.followup, before)
        result = HandoffOutboxWorker(
            store=container.followup, delivery=delivery, worker_id="rollback-worker",
            lease_ttl=timedelta(seconds=30),
        ).run_once(now=late)
        assert result.disposition.value == ("delivered" if state == "pending" else "idle")
        assert len(delivery.messages) == (1 if state == "pending" else 0)
        expected = "manual_review" if state in ("leased", "consumed", "manual_review") else "delivered"
        assert container.followup._connection.execute(
            "SELECT status FROM handoff_outbox"
        ).fetchone() == (expected,)
        if state in ("consumed", "leased"):
            assert container.followup._connection.execute(
                "SELECT dispatch_slots_consumed FROM handoff_outbox"
            ).fetchone() == (1,)
    finally:
        container.close()
    # Roll forward remains possible after rollback wrote receipt/manual-review.
    after = immutable_writer(CANDIDATE, path, "inspect")
    container = V2Container.open(settings=settings, role=V2Role.WORKER)
    try:
        assert_preserved(container.followup, after)
        assert HandoffOutboxWorker(
            store=container.followup, delivery=delivery, worker_id="rollback-restart",
            lease_ttl=timedelta(seconds=30),
        ).run_once(now=late + timedelta(seconds=31)).disposition.value == "idle"
        assert len(delivery.messages) == (1 if state == "pending" else 0)
    finally:
        container.close()
