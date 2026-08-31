from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import v2_ops.app as ops_app
from v2_ops.auth import hash_password
from v2_ops.app import create_ops_app
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, OpsNodeFinish, OpsNodeStart
from v2_ops.settings import OpsWebSettings
from v2_ops.store import OpsTraceStoreError, SQLiteOpsTraceReader, SQLiteOpsTraceWriter


NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def _build_client(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    now: datetime | None = None,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, str, str]:
    path = (tmp_path / "ops.sqlite3").resolve()
    key = b"t" * 32
    writer = SQLiteOpsTraceWriter(path, key)
    if now is not None:
        if monkeypatch is None:
            raise ValueError("monkeypatch is required with fixed now")
        monkeypatch.setattr(ops_app, "_now", lambda: now)
    started = (
        now - timedelta(hours=12)
        if now is not None
        else datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
    )
    execution = OpsExecution("event-1", "manychat:123", started)
    writer.write_execution(execution)
    node = OpsNodeStart(
        execution_id=execution.execution_id,
        node_type=NodeType.MAYA_REQUEST,
        ordinal=1,
        started_at=started,
        input_summary={"request_id": "request-1", "message_hash": "a" * 64},
        input_full={"request_id": "request-1"},
    )
    writer.start_node(node)
    writer.finish_node(
        OpsNodeFinish.from_start(
            node,
            status=ExecutionStatus.COMPLETED,
            completed_at=started + timedelta(seconds=1),
            output_summary={"status": "ok"},
            output_full={"status": "ok"},
        )
    )
    writer.write_execution(
        replace(
            execution,
            status=ExecutionStatus.COMPLETED,
            current_node_id=node.node_id,
            completed_at=started + timedelta(seconds=1),
            terminal_reason="turn_committed",
        )
    )
    if now is not None:
        writer.write_execution(
            OpsExecution("event-week", "manychat:week", now - timedelta(days=3))
        )
        writer.write_execution(
            OpsExecution("event-month", "manychat:month", now - timedelta(days=15))
        )
    writer.close()
    settings = OpsWebSettings(
        username="ops",
        password_hash=hash_password("password-123", salt=b"s" * 16),
        session_key=b"k" * 32,
        trace_path=path,
        trace_key=key,
        secure_cookie=True,
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        config_fingerprint="c" * 64,
    )
    app = create_ops_app(settings, reader=SQLiteOpsTraceReader(path, key))
    return (
        TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=raise_server_exceptions,
        ),
        "event-1",
        node.node_id,
    )


def _login(client: TestClient) -> str:
    page = client.get("/ops/login")
    assert page.status_code == 200
    csrf = page.cookies["v2_ops_csrf"]
    response = client.post(
        "/ops/login",
        data={"username": "ops", "password": "password-123", "csrf": csrf},
        headers={"Origin": "https://testserver", "Referer": "https://testserver/ops/login"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/ops" in cookie
    return csrf


def test_only_login_and_health_are_public(tmp_path: Path) -> None:
    client, execution_id, _ = _build_client(tmp_path)

    assert client.get("/ops/healthz").status_code == 200
    login = client.get("/ops/login")
    assert login.status_code == 200
    assert client.get("/ops/static/ops.css").status_code == 200
    assert client.get("/ops", follow_redirects=False).headers["location"] == "/ops/"
    assert client.get("/ops/", follow_redirects=False).status_code == 303
    assert client.get("/ops/api/executions").status_code == 401
    assert client.get("/ops/api/records").status_code == 401
    assert client.get("/ops/api/leads/manychat%3A123").status_code == 401
    assert client.get("/ops/api/exports/leads.csv").status_code == 401
    assert client.get(f"/ops/api/executions/{execution_id}").status_code == 401
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_authenticated_harness_and_release_metadata_are_bounded(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    assert client.get("/ops/api/harness").status_code == 401
    assert client.get("/ops/api/release").status_code == 401
    _login(client)

    harness = client.get("/ops/api/harness")
    release = client.get("/ops/api/release")

    assert harness.status_code == 200
    assert harness.json() == {"available": False, "execution_id": None}
    assert release.json() == {
        "release_sha": "a" * 40,
        "image_digest": "sha256:" + "b" * 64,
        "config_fingerprint": "c" * 64,
    }


def test_dashboard_requires_session_and_rejects_open_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = _build_client(tmp_path, monkeypatch=monkeypatch, now=NOW)
    calls = {"auth": 0, "parse": 0, "reader": 0}
    original_verify = ops_app.SessionCodec.verify

    def verify(*args: object, **kwargs: object) -> object:
        calls["auth"] += 1
        return original_verify(*args, **kwargs)

    def parse(*args: object, **kwargs: object) -> object:
        calls["parse"] += 1
        raise AssertionError("range must not be parsed before authentication")

    def read(*args: object, **kwargs: object) -> object:
        calls["reader"] += 1
        raise AssertionError("reader must not run before authentication")

    monkeypatch.setattr(ops_app.SessionCodec, "verify", verify)
    monkeypatch.setattr(ops_app.DashboardRange, "parse", classmethod(parse))
    monkeypatch.setattr(SQLiteOpsTraceReader, "list_dashboard_records", read)
    unauthenticated = client.get("/ops/api/dashboard?range=today")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"status": "authentication_required"}
    assert calls == {"auth": 1, "parse": 0, "reader": 0}

    monkeypatch.undo()
    monkeypatch.setattr(ops_app, "_now", lambda: NOW)
    _login(client)
    invalid = client.get("/ops/api/dashboard?range=today")
    assert invalid.status_code == 422
    assert invalid.json() == {"status": "invalid_query"}


def test_dashboard_payload_is_real_bounded_and_etagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, node_id = _build_client(tmp_path, monkeypatch=monkeypatch, now=NOW)
    _login(client)

    response = client.get("/ops/api/dashboard?range=7d")

    assert response.status_code == 200
    assert response.json() == {
        "generated_at": "2026-08-21T12:00:00Z",
        "range": "7d",
        "metrics": {
            "executions": 2,
            "distinct_leads": 2,
            "in_progress": 1,
            "completed": 1,
            "failed": 0,
            "manual_review": 0,
            "technical_completion_rate": 50.0,
            "average_terminal_duration_ms": 1000.0,
        },
        "execution_series": [
            {"start_at": "2026-08-14T12:00:00Z", "count": 0},
            {"start_at": "2026-08-15T12:00:00Z", "count": 0},
            {"start_at": "2026-08-16T12:00:00Z", "count": 0},
            {"start_at": "2026-08-17T12:00:00Z", "count": 0},
            {"start_at": "2026-08-18T12:00:00Z", "count": 1},
            {"start_at": "2026-08-19T12:00:00Z", "count": 0},
            {"start_at": "2026-08-20T12:00:00Z", "count": 1},
        ],
        "status_distribution": [
            {"status": "pending", "count": 1},
            {"status": "running", "count": 0},
            {"status": "running_stale", "count": 0},
            {"status": "completed", "count": 1},
            {"status": "failed", "count": 0},
            {"status": "manual_review", "count": 0},
        ],
        "trace_distribution": [
            {"trace_completeness": "complete_trace", "count": 2},
            {"trace_completeness": "partial_trace", "count": 0},
            {"trace_completeness": "ledger_only", "count": 0},
        ],
        "milestones": [
            {"milestone": "reservation", "count": 0},
            {"milestone": "payment", "count": 0},
            {"milestone": "public_delivery", "count": 0},
            {"milestone": "handoff", "count": 0},
        ],
        "top_node_types": [{"node_type": "maya_request", "count": 1}],
        "executions": [
            {
                "lead_id": "manychat:123",
                "execution_id": "event-1",
                "received_at": "2026-08-21T00:00:00Z",
                "duration_ms": 1000.0,
                "status": "completed",
                "trace_completeness": "complete_trace",
                "current_node_type": "maya_request",
                "node_count": 1,
                "has_reservation": False,
                "has_payment": False,
                "has_public_delivery": False,
                "has_handoff": False,
                "terminal_reason": "turn_committed",
            },
            {
                "lead_id": "manychat:week",
                "execution_id": "event-week",
                "received_at": "2026-08-18T12:00:00Z",
                "duration_ms": None,
                "status": "pending",
                "trace_completeness": "complete_trace",
                "current_node_type": None,
                "node_count": 0,
                "has_reservation": False,
                "has_payment": False,
                "has_public_delivery": False,
                "has_handoff": False,
                "terminal_reason": None,
            },
        ],
    }
    assert client.get(
        "/ops/api/dashboard?range=7d",
        headers={"If-None-Match": response.headers["etag"]},
    ).status_code == 304
    assert client.get("/ops/api/dashboard?range=24h").json()["metrics"]["executions"] == 1
    assert client.get("/ops/api/dashboard?range=30d").json()["metrics"]["executions"] == 3
    assert node_id not in response.text


def test_dashboard_cache_keeps_body_and_etag_for_exact_two_second_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instant = [NOW]
    client, _, _ = _build_client(tmp_path)
    monkeypatch.setattr(ops_app, "_now", lambda: instant[0])
    _login(client)
    reads = 0
    original_read = SQLiteOpsTraceReader.list_dashboard_records

    def read(*args: object, **kwargs: object) -> object:
        nonlocal reads
        reads += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(SQLiteOpsTraceReader, "list_dashboard_records", read)
    first = client.get("/ops/api/dashboard?range=7d")
    assert first.status_code == 200
    first_etag = first.headers["etag"]
    assert reads == 1

    instant[0] = NOW + timedelta(seconds=1)
    within_ttl = client.get(
        "/ops/api/dashboard?range=7d", headers={"If-None-Match": first_etag}
    )
    assert within_ttl.status_code == 304
    assert within_ttl.headers["etag"] == first_etag
    assert reads == 1

    instant[0] = NOW + timedelta(seconds=2)
    expired = client.get(
        "/ops/api/dashboard?range=7d", headers={"If-None-Match": first_etag}
    )
    assert expired.status_code == 200
    assert expired.headers["etag"] != first_etag
    assert expired.json()["generated_at"] == "2026-08-21T12:00:02Z"
    assert reads == 2


def test_dashboard_projection_omits_recursive_internal_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = _build_client(tmp_path, monkeypatch=monkeypatch, now=NOW)
    _login(client)
    original_build = ops_app.build_dashboard_snapshot

    def with_internal_sentinels(*args: object, **kwargs: object) -> object:
        snapshot = original_build(*args, **kwargs)

        def marked(values: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
            return tuple({**item, "_internal": "must-not-leak"} for item in values)

        return replace(
            snapshot,
            metrics={**snapshot.metrics, "_internal": "must-not-leak"},
            execution_series=marked(snapshot.execution_series),
            status_distribution=marked(snapshot.status_distribution),
            trace_distribution=marked(snapshot.trace_distribution),
            milestones=marked(snapshot.milestones),
            top_node_types=marked(snapshot.top_node_types),
            executions=marked(snapshot.executions),
        )

    monkeypatch.setattr(ops_app, "build_dashboard_snapshot", with_internal_sentinels)
    payload = client.get("/ops/api/dashboard?range=7d").json()

    assert set(payload["metrics"]) == {
        "executions",
        "distinct_leads",
        "in_progress",
        "completed",
        "failed",
        "manual_review",
        "technical_completion_rate",
        "average_terminal_duration_ms",
    }
    expected_nested_keys = {
        "execution_series": {"start_at", "count"},
        "status_distribution": {"status", "count"},
        "trace_distribution": {"trace_completeness", "count"},
        "milestones": {"milestone", "count"},
        "top_node_types": {"node_type", "count"},
        "executions": {
            "lead_id",
            "execution_id",
            "received_at",
            "duration_ms",
            "status",
            "trace_completeness",
            "current_node_type",
            "node_count",
            "has_reservation",
            "has_payment",
            "has_public_delivery",
            "has_handoff",
            "terminal_reason",
        },
    }
    for section, keys in expected_nested_keys.items():
        assert payload[section]
        assert all(set(item) == keys for item in payload[section])


@pytest.mark.parametrize("failure", [ValueError, TypeError])
@pytest.mark.parametrize("boundary", ["reader", "builder"])
def test_dashboard_sanitizes_invalid_source_or_builder_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    failure: type[Exception],
) -> None:
    client, _, _ = _build_client(
        tmp_path,
        monkeypatch=monkeypatch,
        now=NOW,
        raise_server_exceptions=False,
    )
    _login(client)

    def fail(*args: object, **kwargs: object) -> object:
        raise failure("private invalid data detail")

    if boundary == "reader":
        monkeypatch.setattr(SQLiteOpsTraceReader, "list_dashboard_records", fail)
    else:
        monkeypatch.setattr(ops_app, "build_dashboard_snapshot", fail)
    response = client.get("/ops/api/dashboard?range=7d")
    assert response.status_code == 503
    assert response.json() == {"status": "source_unavailable"}
    assert "private invalid data detail" not in response.text


def test_dashboard_sanitizes_reader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = _build_client(tmp_path, monkeypatch=monkeypatch, now=NOW)
    _login(client)

    def fail(*args: object, **kwargs: object) -> object:
        raise OpsTraceStoreError("private database detail")

    monkeypatch.setattr(SQLiteOpsTraceReader, "list_dashboard_records", fail)
    response = client.get("/ops/api/dashboard?range=7d")
    assert response.status_code == 503
    assert response.json() == {"status": "source_unavailable"}
    assert "private database detail" not in response.text


def test_authenticated_list_detail_nodes_full_and_etag_are_read_only(tmp_path: Path) -> None:
    client, execution_id, node_id = _build_client(tmp_path)
    _login(client)

    page = client.get("/ops/")
    assert page.status_code == 200
    assert "Painel operacional" in page.text

    executions = client.get("/ops/api/executions?lead_id=manychat%3A123&limit=10")
    assert executions.status_code == 200
    assert executions.json()["executions"][0]["execution_id"] == execution_id
    etag = executions.headers["etag"]
    assert client.get(
        "/ops/api/executions?lead_id=manychat%3A123&limit=10",
        headers={"If-None-Match": etag},
    ).status_code == 304
    assert client.get("/ops/api/executions?status=completed").status_code == 200

    detail = client.get(f"/ops/api/executions/{execution_id}")
    assert detail.status_code == 200
    nodes = client.get(f"/ops/api/executions/{execution_id}/nodes")
    assert nodes.json()["nodes"][0]["node_id"] == node_id
    assert client.get(
        f"/ops/api/executions/{execution_id}/nodes/{node_id}/full?side=input"
    ).json()["value"] == {"request_id": "request-1"}

    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"/ops/api/executions/{execution_id}").status_code in {404, 405}
    assert client.post("/ops/api/executions").status_code in {404, 405}


def test_login_rejects_bad_csrf_origin_and_credentials_without_cause(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    page = client.get("/ops/login")
    csrf = page.cookies["v2_ops_csrf"]

    bad_origin = client.post(
        "/ops/login",
        data={"username": "ops", "password": "password-123", "csrf": csrf},
        headers={"Origin": "https://evil.example"},
    )
    assert bad_origin.status_code == 403
    bad_password = client.post(
        "/ops/login",
        data={"username": "ops", "password": "wrong", "csrf": csrf},
        headers={"Origin": "https://testserver", "Referer": "https://testserver/ops/login"},
    )
    assert bad_password.status_code == 401
    assert bad_password.json() == {"status": "invalid_credentials"}


def test_login_accepts_valid_csrf_when_browser_omits_origin_and_referer(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    page = client.get("/ops/login")
    csrf = page.cookies["v2_ops_csrf"]

    response = client.post(
        "/ops/login",
        data={"username": "ops", "password": "password-123", "csrf": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ops/"


def test_login_accepts_null_origin_with_valid_csrf(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    page = client.get("/ops/login")
    csrf = page.cookies["v2_ops_csrf"]

    response = client.post(
        "/ops/login",
        data={"username": "ops", "password": "password-123", "csrf": csrf},
        headers={"Origin": "null"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_login_accepts_matching_csrf_among_duplicate_browser_cookies(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    page = client.get("/ops/login")
    csrf = page.cookies["v2_ops_csrf"]

    response = client.post(
        "/ops/login",
        data={"username": "ops", "password": "password-123", "csrf": csrf},
        headers={
            "Origin": "https://testserver",
            "Cookie": f"v2_ops_csrf={csrf}; v2_ops_csrf=stale-browser-cookie",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_security_headers_and_sse_require_session(tmp_path: Path) -> None:
    client, _, _ = _build_client(tmp_path)
    unauthenticated = client.get("/ops/api/events")
    assert unauthenticated.status_code == 401
    _login(client)
    response = client.get("/ops/static/ops.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
