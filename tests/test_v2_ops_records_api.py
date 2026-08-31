from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import inspect
import json
import sqlite3
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import v2_ops.app as ops_app
from tests.v2_ops_records_fixture import write_records_fixture
from v2_ops.app import create_ops_app
from v2_ops.auth import hash_password
from v2_ops.contracts import ExecutionStatus, OpsExecution
from v2_ops.records import RecordsSourceError, SQLiteRecordsReader
from v2_ops.settings import OpsWebSettings
from v2_ops.store import SQLiteOpsTraceReader, SQLiteOpsTraceWriter


NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


def _build_records_client(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch | None = None,
    now: datetime | None = None,
) -> tuple[TestClient, Path, object]:
    trace_path = (tmp_path / "ops.sqlite3").resolve()
    trace_key = b"t" * 32
    records_path = (tmp_path / "records").resolve()
    identity = write_records_fixture(records_path)

    writer = SQLiteOpsTraceWriter(trace_path, trace_key)
    execution = OpsExecution(
        "execution-records-001",
        identity.lead_id,
        datetime(2026, 8, 30, 11, 56, tzinfo=timezone.utc),
    )
    writer.write_execution(execution)
    writer.write_execution(
        replace(
            execution,
            status=ExecutionStatus.COMPLETED,
            completed_at=NOW,
            terminal_reason="turn_committed",
        )
    )
    writer.close()

    if now is not None:
        if monkeypatch is None:
            raise ValueError("monkeypatch is required with fixed now")
        monkeypatch.setattr(ops_app, "_now", lambda: now)

    settings = OpsWebSettings(
        username="ops",
        password_hash=hash_password("password-123", salt=b"s" * 16),
        session_key=b"k" * 32,
        trace_path=trace_path,
        trace_key=trace_key,
        secure_cookie=True,
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        config_fingerprint="c" * 64,
        records_path=records_path,
    )
    app = create_ops_app(
        settings,
        reader=SQLiteOpsTraceReader(trace_path, trace_key),
        records_reader=SQLiteRecordsReader(records_path),
    )
    return TestClient(app, base_url="https://testserver"), records_path, identity


def _login(client: TestClient) -> None:
    page = client.get("/ops/login")
    csrf = page.cookies["v2_ops_csrf"]
    response = client.post(
        "/ops/login",
        data={"username": "ops", "password": "password-123", "csrf": csrf},
        headers={
            "Origin": "https://testserver",
            "Referer": "https://testserver/ops/login",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_records_authentication_precedes_query_parse_and_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _root, _identity = _build_records_client(tmp_path)

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("records reader ran before authentication")

    monkeypatch.setattr(SQLiteRecordsReader, "snapshot", fail)
    response = client.get("/ops/api/records?unknown=1")
    export = client.get("/ops/api/exports/unknown.csv?unknown=1")

    assert response.status_code == 401
    assert response.json() == {"status": "authentication_required"}
    assert export.status_code == 401
    assert export.json() == {"status": "authentication_required"}


def test_records_and_lead_detail_are_allowlisted_etagged_and_factual(
    tmp_path: Path,
) -> None:
    client, _root, identity = _build_records_client(tmp_path)
    _login(client)

    records = client.get("/ops/api/records")

    assert records.status_code == 200
    assert set(records.json()) == {
        "generated_at",
        "leads",
        "reservations",
        "payments",
        "handoffs",
        "truncated",
    }
    payload = records.json()
    assert payload["truncated"] is False
    lead = next(item for item in payload["leads"] if item["lead_id"] == identity.lead_id)
    assert lead["state_code"] == "reservation_confirmed"
    assert payload["reservations"][0]["status_code"] == "confirmed"
    assert payload["reservations"][0]["bokun_booking_id"] is None
    assert payload["reservations"][0]["cloudbeds_reservation_id"] is None
    assert payload["payments"][0]["record_id"] == identity.initiation_id
    assert payload["payments"][0]["status_code"] == "link_ready"
    assert payload["payments"][0]["amount_paid_minor"] is None
    assert payload["handoffs"] == []
    assert client.get(
        "/ops/api/records",
        headers={"If-None-Match": records.headers["etag"]},
    ).status_code == 304

    detail = client.get(f"/ops/api/leads/{quote(identity.lead_id, safe='')}")
    assert detail.status_code == 200
    assert set(detail.json()) == {"lead"}
    lead_detail = detail.json()["lead"]
    assert set(lead_detail) == {
        "summary",
        "facts",
        "dialogue_turns",
        "passenger_manifests",
        "inbound_events",
        "public_replies",
        "reservations",
        "payments",
        "handoffs",
        "executions",
        "truncated",
    }
    assert lead_detail["summary"]["lead_id"] == identity.lead_id
    assert len(lead_detail["facts"]) == 3
    assert lead_detail["dialogue_turns"][0]["customer_message"]
    assert lead_detail["inbound_events"][0]["payload_exposed"] is False
    assert lead_detail["reservations"][0]["command_id"] == identity.command_id
    assert lead_detail["payments"][0]["payment_id"] == identity.payment_id
    assert lead_detail["executions"][0]["execution_id"] == "execution-records-001"
    assert lead_detail["truncated"] is False

    for forbidden in (
        "claim_token",
        "claim_owner",
        "lease_expires_at",
        "fencing_token",
        "payload_hash",
        "command_hash",
        "state_hash",
        "selection_hash",
        "result_json",
        "receipt_json",
        "ciphertext",
        "nonce",
    ):
        assert forbidden not in records.text
        assert forbidden not in detail.text


def test_records_lists_are_capped_at_200_and_report_truncation(tmp_path: Path) -> None:
    client, root, _identity = _build_records_client(tmp_path)
    connection = sqlite3.connect(root / "inbox.sqlite3")
    connection.executemany(
        "INSERT INTO inbound_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"inbound-extra-{index:03d}",
                f"manychat:extra-{index:03d}",
                f"subscriber-extra-{index:03d}",
                f"conversation-extra-{index:03d}",
                f"2026-08-29T10:{index % 60:02d}:00+00:00",
                sqlite3.Binary(b"excluded"),
                f"{index:064x}"[-64:],
                "processed",
                None,
                None,
                None,
                f"2026-08-29T10:{index % 60:02d}:01+00:00",
            )
            for index in range(201)
        ],
    )
    connection.commit()
    connection.close()
    _login(client)

    response = client.get("/ops/api/records")

    assert response.status_code == 200
    assert len(response.json()["leads"]) == 200
    assert response.json()["truncated"] is True


def test_unknown_query_dataset_id_and_source_failures_are_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _root, _identity = _build_records_client(
        tmp_path,
        monkeypatch=monkeypatch,
        now=NOW,
    )
    _login(client)

    assert client.get("/ops/api/records?range=7d").status_code == 422
    assert client.get("/ops/api/leads/%00bad").status_code in {404, 422}
    assert client.get("/ops/api/leads/manychat%3Amissing").status_code == 404
    assert client.get("/ops/api/exports/unknown.csv").status_code == 422
    assert client.get("/ops/api/exports/leads.csv?lead_id=x").status_code == 422
    assert client.get("/ops/api/exports/lead-history.csv").status_code == 422

    def fail(*args: object, **kwargs: object) -> object:
        raise RecordsSourceError()

    monkeypatch.setattr(SQLiteRecordsReader, "snapshot", fail)
    monkeypatch.setattr(ops_app, "_now", lambda: NOW + timedelta(seconds=2))
    response = client.get("/ops/api/records")
    assert response.status_code == 503
    assert response.json() == {"status": "records_source_unavailable"}
    assert "records source unavailable" not in response.text


def test_records_cache_has_exact_two_second_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instant = [NOW]
    client, _root, _identity = _build_records_client(tmp_path)
    monkeypatch.setattr(ops_app, "_now", lambda: instant[0])
    _login(client)
    calls = 0
    original = SQLiteRecordsReader.snapshot

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(SQLiteRecordsReader, "snapshot", counted)
    first = client.get("/ops/api/records")
    assert first.status_code == 200
    assert calls == 1

    instant[0] = NOW + timedelta(seconds=1)
    cached = client.get(
        "/ops/api/records", headers={"If-None-Match": first.headers["etag"]}
    )
    assert cached.status_code == 304
    assert calls == 1

    instant[0] = NOW + timedelta(seconds=2)
    expired = client.get(
        "/ops/api/records", headers={"If-None-Match": first.headers["etag"]}
    )
    assert expired.status_code == 200
    assert calls == 2
    assert expired.headers["etag"] != first.headers["etag"]


def test_sse_combines_records_change_token_without_second_endpoint() -> None:
    source = inspect.getsource(create_ops_app)

    assert source.count('@app.get("/ops/api/events")') == 1
    assert "commercial_reader.change_token()" in source
    assert '"records_change_token"' in source


@pytest.mark.parametrize(
    "dataset",
    ["leads", "executions", "reservations", "payments", "handoffs"],
)
def test_closed_csv_datasets_are_bom_prefixed_and_downloadable(
    tmp_path: Path,
    dataset: str,
) -> None:
    client, _root, _identity = _build_records_client(tmp_path)
    assert client.get(f"/ops/api/exports/{dataset}.csv").status_code == 401
    _login(client)

    response = client.get(f"/ops/api/exports/{dataset}.csv")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith(
        f'filename="maya-ops-{dataset}.csv"'
    )


def test_csv_formula_cells_are_neutralized_and_lead_history_requires_exact_id(
    tmp_path: Path,
) -> None:
    client, root, identity = _build_records_client(tmp_path)
    connection = sqlite3.connect(root / "v2-private-customer.sqlite3")
    connection.execute(
        "UPDATE private_customer_facts SET private_value='=danger' "
        "WHERE lead_id=? AND fact_name='full_name'",
        (identity.lead_id,),
    )
    connection.commit()
    connection.close()
    _login(client)

    missing = client.get("/ops/api/exports/lead-history.csv")
    response = client.get(
        f"/ops/api/exports/lead-history.csv?lead_id={quote(identity.lead_id, safe='')}"
    )

    assert missing.status_code == 422
    assert response.status_code == 200
    assert b"'=danger" in response.content
    assert b"=danger," not in response.content


def test_lead_history_flattens_manifest_without_raw_json(tmp_path: Path) -> None:
    client, root, identity = _build_records_client(tmp_path)
    manifest = {
        "schema": "v2-passenger-manifest-v1",
        "adults": 1,
        "children": 0,
        "passengers": [
            {
                "position": 1,
                "participant_type": "adult",
                "full_name": "Viajante Fixture",
                "birth_date": "1990-01-01",
                "gender": None,
                "country_code": "BR",
            }
        ],
    }
    connection = sqlite3.connect(root / "v2-private-customer.sqlite3")
    connection.execute(
        "INSERT INTO private_passenger_manifests "
        "(lead_id, fact_json, fact_hash, source_turn_id, source_event_hash, revision, persisted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            identity.lead_id,
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
            "a" * 64,
            "turn-manifest-001",
            "b" * 64,
            1,
            NOW.isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    _login(client)

    response = client.get(
        f"/ops/api/exports/lead-history.csv?lead_id={quote(identity.lead_id, safe='')}"
    )

    assert response.status_code == 200
    assert b"Viajante Fixture" in response.content
    assert b"1990-01-01" in response.content
    assert b"country_code=BR" in response.content
    assert b"v2-passenger-manifest-v1" not in response.content
    assert b'manifest_json' not in response.content
    assert b'\"schema\"' not in response.content


def test_export_projection_failure_is_sanitized_as_source_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _root, _identity = _build_records_client(tmp_path)
    _login(client)

    def fail_projection(*_args: object, **_kwargs: object) -> bytes:
        raise ValueError("private projection detail")

    monkeypatch.setattr(ops_app, "render_csv", fail_projection)
    response = client.get("/ops/api/exports/leads.csv")

    assert response.status_code == 503
    assert response.json() == {"status": "records_source_unavailable"}
    assert "private projection detail" not in response.text
