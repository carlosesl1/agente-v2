from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from tests.test_v2_ops_records_api import NOW, _build_records_client, _login
from v2_ops.contracts import OpsExecution
from v2_ops.store import SQLiteOpsTraceWriter


@pytest.mark.parametrize("same_lead_count", [0, 1, 200, 205])
def test_lead_history_is_independent_of_unrelated_executions(
    tmp_path: Path, same_lead_count: int
) -> None:
    client, _root, identity = _build_records_client(tmp_path)
    trace_path = tmp_path / "ops.sqlite3"
    with sqlite3.connect(trace_path) as connection:
        connection.execute("DELETE FROM executions")
    with SQLiteOpsTraceWriter(trace_path, b"t" * 32) as writer:
        for index in range(same_lead_count):
            writer.write_execution(
                OpsExecution(
                    f"target-{index:04d}",
                    identity.lead_id,
                    NOW + timedelta(seconds=index),
                )
            )
    _login(client)
    url = f"/ops/api/leads/{identity.lead_id}"
    before = client.get(url)
    assert before.status_code == 200
    with SQLiteOpsTraceWriter(trace_path, b"t" * 32) as writer:
        for index in range(201):
            writer.write_execution(
                OpsExecution(
                    f"other-{index:04d}",
                    "synthetic-other",
                    NOW + timedelta(days=1, seconds=index),
                )
            )
    database_before = trace_path.read_bytes()
    after = client.get(url)
    assert after.status_code == 200
    lead = after.json()["lead"]
    assert lead == before.json()["lead"]
    assert lead["summary"]["execution_count"] == min(same_lead_count, 200)
    assert len(lead["executions"]) == min(same_lead_count, 200)
    assert lead["truncated"] is (same_lead_count > 200)
    assert [item["execution_id"] for item in lead["executions"]] == [
        f"target-{index:04d}"
        for index in range(same_lead_count - 1, max(-1, same_lead_count - 201), -1)
    ]
    assert client.get("/ops/api/leads/synthetic-unknown").status_code == 404
    assert client.get("/ops/api/leads/").status_code == 404
    page = client.get("/ops/")
    assert page.status_code == 200
    assert 'id="lead-detail"' in page.text
    assert 'id="lead-executions"' in page.text
    assert 'id="lead-detail-summary"' in page.text
    assert trace_path.read_bytes() == database_before
