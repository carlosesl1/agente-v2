from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from v2_ops.auth import hash_password
from v2_ops.app import create_ops_app
from v2_ops.contracts import ExecutionStatus, NodeType, OpsExecution, OpsNodeFinish, OpsNodeStart
from v2_ops.settings import OpsWebSettings
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


def _build_client(tmp_path: Path) -> tuple[TestClient, str, str]:
    path = (tmp_path / "ops.sqlite3").resolve()
    key = b"t" * 32
    writer = SQLiteOpsTraceWriter(path, key)
    started = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)
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
    return TestClient(app, base_url="https://testserver"), "event-1", node.node_id


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


def test_authenticated_list_detail_nodes_full_and_etag_are_read_only(tmp_path: Path) -> None:
    client, execution_id, node_id = _build_client(tmp_path)
    _login(client)

    page = client.get("/ops/")
    assert page.status_code == 200
    assert "Execution Canvas" in page.text

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
