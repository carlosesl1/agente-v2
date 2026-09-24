"""Official postPayment receipt contract over controlled HTTP, real fence/store."""
import hashlib
import io
import json
import logging
from datetime import timedelta

import httpx
import pytest

from tests.test_v2_stripe_settlement import lab, worker  # noqa: F401
from reservation_followup.sqlite_store import SQLiteFollowupUnitOfWork
from v2_adapters.execution_context import component_wire
from v2_application.active_execution import ReservationExecutionStatusResolver


@pytest.mark.parametrize("field,value", [
    ("paymentID", True), ("paymentID", {"id": "123"}),
    ("transactionID", ["txn123"]), ("transactionID", "  "),
    ("paymentID", ""), ("transactionID", None),
])
def test_malformed_receipt_identity_is_not_registered_as_paid(lab, field, value):
    original = lab.provider_client._transport.handler
    payload = {"success": True, "paymentID": "pay123", "transactionID": "txn123"}
    payload[field] = value
    def handler(request):
        if request.method == "POST":
            lab.provider_requests.append(request)
            return httpx.Response(200, json=payload)
        return original(request)
    lab.provider_client._transport.handler = handler
    assert worker(lab).run_once(now=lab.now).disposition.value == "manual_review"
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == 1
    assert worker(lab).run_once(now=lab.now + timedelta(seconds=100)).disposition.value == "idle"


def test_receipt_diagnostic_survives_plain_log_formatter(lab):
    original = lab.provider_client._transport.handler
    body = b'{"success":true,"paymentID":"pay123"}'
    def handler(request):
        if request.method == "POST":
            lab.provider_requests.append(request)
            return httpx.Response(200, content=body, headers={"X-Request-ID": "request-fixture"})
        return original(request)
    lab.provider_client._transport.handler = handler
    output = io.StringIO()
    stream = logging.StreamHandler(output)
    stream.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("v2_host.stripe_settlement")
    logger.addHandler(stream)
    try:
        assert worker(lab).run_once(now=lab.now).disposition.value == "manual_review"
    finally:
        logger.removeHandler(stream)
    record = output.getvalue()
    assert hashlib.sha256(body).hexdigest() in record
    assert '"transactionID":"NoneType"' in record
    assert '"http_status":200' in record
    assert 'request-fixture' in record


def test_documented_receipt_reaches_maya_and_reopen_without_second_post(lab):
    assert worker(lab).run_once(now=lab.now).disposition.value == "settled"
    lab.followup.close()
    lab.followup = SQLiteFollowupUnitOfWork.open_v2(lab.paths["followup"])
    command, ledger = lab.execution.list_outcome_projection_inputs()[0]
    resolver = ReservationExecutionStatusResolver(lab.execution, followup=lab.followup)
    wire = component_wire(resolver._component(command, ledger))
    settlement = wire["payment"]["settlements"][0]
    assert settlement["status"] == "paid"
    assert settlement["certainty"] == "settled"
    assert settlement["verified_payment"]["status"] == "captured"
    assert worker(lab).run_once(now=lab.now + timedelta(seconds=100)).disposition.value == "idle"
    assert len([r for r in lab.provider_requests if r.method == "POST"]) == 1
