from datetime import date, timedelta
from decimal import Decimal
import json

import httpx
import pytest

from reservation_domain import ExecutionCertainty
from reservation_execution.sqlite_store import SQLiteUnitOfWork
from tests.test_v2_outcome_projector import NOW, _stores, _package_command, _finish_next
from tests.test_v2_inbox_relay_workers import OneRelaySource, _source_receipt
from v2_adapters.provider_http import (
    CloudbedsHTTPTransport, ProviderHTTPError, _validate_cloudbeds_rate_revalidation,
)
from v2_application.completion import PublicOutboxStore
from v2_application.completion_projector import CompletionProjector
from v2_application.relay_worker import BoundaryRelayWorker, CommandRelayClaim, build_reservation_relay_bundle, reservation_target_operation_id
from v2_application.reservations import ReservationAllocator


def _relay(execution, commands):
    bundles = tuple(build_reservation_relay_bundle(c) for c in commands)
    relay_ids = ('relay:commercial',) + tuple('relay:commercial:' + str(i) for i in range(1, len(bundles)))
    proof = _source_receipt(bundles, relay_ids)
    source_hash = proof.artifact_hash
    first = bundles[0]
    claim = CommandRelayClaim(
        relay_id='relay:commercial', command_id=commands[0].command_id,
        bundle_bytes=first.to_canonical_bytes(), bundle_hash=first.artifact_hash,
        source_turn_receipt_hash=source_hash,
        target_operation_id=reservation_target_operation_id(bundle_hash=first.artifact_hash, source_turn_receipt_hash=source_hash),
        worker_id='worker:commercial', fencing_token=1,
        lease_expires_at=NOW + timedelta(seconds=30),
    )
    class Source(OneRelaySource):
        def load_command_relay_group(self, claim):
            return proof, bundles
        def release_command_relay(self, claim, *, now):
            pass
    source = Source(claim)
    return BoundaryRelayWorker(boundary=source, reservation_target=execution, worker_id='worker:commercial', lease_ttl=timedelta(seconds=30))


@pytest.mark.parametrize('success', [True, False])
def test_package_relay_registers_complete_group_before_projection_and_restart(tmp_path, success):
    execution, payments, projector = _stores(tmp_path)
    public = PublicOutboxStore(tmp_path / 'public.sqlite3')
    completion = CompletionProjector(execution=execution, payment_store=None, public_store=public, account_profiles=None, subscriber_id='123456789', include_payment_offers=False)
    commands = ReservationAllocator().allocate(_package_command()).commands
    try:
        _relay(execution, commands).run_once(now=NOW)
        _finish_next(execution, now=NOW + timedelta(seconds=1), certainty=ExecutionCertainty.EFFECT_CONFIRMED)
        assert projector.run_once(now=NOW + timedelta(seconds=2)).inserted == 0
        assert completion.run_once(now=NOW + timedelta(seconds=2)).inserted == 0
        execution.close()
        execution = SQLiteUnitOfWork.open_v6(tmp_path / 'execution.sqlite3')
        projector._execution = completion._execution = execution
        _finish_next(execution, now=NOW + timedelta(seconds=3), certainty=ExecutionCertainty.EFFECT_CONFIRMED if success else ExecutionCertainty.CALLED_NO_EFFECT)
        assert projector.run_once(now=NOW + timedelta(seconds=4)).inserted == (2 if success else 0)
        assert completion.run_once(now=NOW + timedelta(seconds=4)).inserted == (1 if success else 0)
        _relay(execution, commands).run_once(now=NOW + timedelta(seconds=5))
        assert projector.run_once(now=NOW + timedelta(seconds=6)).inserted == 0
        assert completion.run_once(now=NOW + timedelta(seconds=6)).inserted == 0
        rows = payments._connection.execute('SELECT selection_json FROM payment_initiations').fetchall()
        ids = [json.loads(row[0])['obligation']['payment_id'] for row in rows]
        assert len(ids) == len(set(ids)) == (2 if success else 0)
    finally:
        execution.close()
        payments.close()
        public.close()


def test_group_relay_failure_rolls_back_first_member(tmp_path):
    execution, payments, _ = _stores(tmp_path)
    commands = ReservationAllocator().allocate(_package_command()).commands
    calls = 0
    def fail(stage):
        nonlocal calls
        if stage == 'after_receipt_before_commit':
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic second member failure')
    # Existing ingress fault hook supplies the real transaction failure witness.
    execution._phase8_reservation_fault_hook = fail
    try:
        with pytest.raises(RuntimeError, match='second member'):
            _relay(execution, commands).run_once(now=NOW)
        assert execution.list_outcome_projection_inputs() == ()
        execution._phase8_reservation_fault_hook = None
        _relay(execution, commands).run_once(now=NOW)
        assert len(execution.list_outcome_projection_inputs()) == 2
    finally:
        execution.close()
        payments.close()


@pytest.mark.parametrize('daily,total,expected', [
    ([('2026-10-01', '100.00')], '300.00', '300.00'),
    ([('2026-10-01', '100.00')] * 3, '300.00', '300.00'),
    ([('2026-10-01', '100.00')] * 3, None, None),
    ([('2026-10-01', '100.00'), ('2026-10-02', '100.00'), ('2026-10-04', '100.00')], None, None),
    ([('2026-10-01', '100.00')], None, None),
    ([('2026-10-01', '100.00'), ('2026-10-02', '100.00'), ('2026-10-03', '100.00')], None, '300.00'),
    ([('2026-10-01', '100.00'), ('2026-10-02', '100.00'), ('2026-10-03', '100.00')], '350.00', '350.00'),
])
def test_cloudbeds_only_complete_daily_coverage_can_supply_missing_total(daily, total, expected):
    row = {'roomTypeID': 'room-a', 'roomRateID': 'rate-a', 'roomTypeName': 'Synthetic room', 'roomsAvailable': 2, 'currency': 'BRL', 'roomRateDetailed': [{'date': day, 'rate': amount} for day, amount in daily]}
    if total is not None:
        row['totalRate'] = total
    options = _read(row)
    assert ([item['total_amount'] for item in options]) == ([] if expected is None else [expected])


@pytest.mark.parametrize('missing', ['currency', 'roomsAvailable'])
def test_cloudbeds_does_not_invent_capacity_or_currency(missing):
    row = {'roomTypeID': 'room-a', 'roomRateID': 'rate-a', 'roomTypeName': 'Synthetic room', 'roomsAvailable': 2, 'currency': 'BRL', 'totalRate': '300.00'}
    del row[missing]
    assert _read(row) == []


def _price_row(amount_key="rate", total=None):
    row = {
        "roomTypeID": "room-a", "roomRateID": "rate-a",
        "roomTypeName": "Synthetic room", "roomsAvailable": 2, "currency": "BRL",
        "dailyRates": [
            {"date": day, amount_key: "100.00"}
            for day in ("2026-10-01", "2026-10-02", "2026-10-03")
        ],
    }
    if total is not None:
        row["totalRate"] = total
    return row


def _revalidate_price(row, amount):
    _validate_cloudbeds_rate_revalidation(
        {"success": True, "data": [row]}, room_type_id="room-a",
        room_rate_id="rate-a", expected_dates=("2026-10-01", "2026-10-02", "2026-10-03"),
        amount=Decimal(amount), currency="BRL",
    )


@pytest.mark.parametrize("amount_key", ["rate", "roomRate", "price", "amount", "total"])
@pytest.mark.parametrize("total", [None, "300.00", "350.00"])
def test_cloudbeds_quote_price_authority_survives_unchanged_revalidation(amount_key, total):
    row = _price_row(amount_key, total)
    expected = total or "300.00"
    assert [option["total_amount"] for option in _read(row)] == [expected]
    _revalidate_price(row, expected)


@pytest.mark.parametrize("total", [None, "350.00"])
def test_cloudbeds_changed_authoritative_price_rejects_revalidation(total):
    row = _price_row(total=total)
    approved = _read(row)[0]["total_amount"]
    if total is None:
        row["dailyRates"][0]["rate"] = "101.00"
    else:
        row["totalRate"] = "351.00"
    with pytest.raises(ProviderHTTPError):
        _revalidate_price(row, approved)


@pytest.mark.parametrize("alias", ["price", "total"])
def test_cloudbeds_daily_amount_alias_contradiction_rejected(alias):
    row = _price_row()
    row["dailyRates"][0][alias] = "101.00"
    with pytest.raises(ProviderHTTPError):
        _read(row)
    with pytest.raises(ProviderHTTPError):
        _revalidate_price(row, "300.00")


@pytest.mark.parametrize("change", [
    {"total": "351.00"}, {"roomTypeID": "other"}, {"roomRateID": "other"},
    {"roomTypeId": "other"}, {"roomRateId": "other"},
    {"roomsAvailable": 0}, {"availableRooms": 0},
    {"currency": "USD"}, {"currencyCode": "USD"},
])
def test_cloudbeds_authoritative_price_preserves_revalidation_guards(change):
    row = _price_row(total="350.00")
    row.update(change)
    with pytest.raises(ProviderHTTPError):
        _revalidate_price(row, "350.00")


@pytest.mark.parametrize("coverage", ["partial", "duplicate", "wrong_date", "unavailable"])
@pytest.mark.parametrize("total", [None, "350.00"])
def test_cloudbeds_prewrite_daily_evidence_remains_strict(coverage, total):
    row = _price_row(total=total)
    if coverage == "partial":
        row["dailyRates"].pop()
    elif coverage == "duplicate":
        row["dailyRates"][1]["date"] = "2026-10-01"
    elif coverage == "wrong_date":
        row["dailyRates"][1]["date"] = "2026-10-04"
    else:
        row["dailyRates"][1]["roomsAvailable"] = 0
    with pytest.raises(ProviderHTTPError):
        _revalidate_price(row, total or "300.00")
    if total is None:
        assert _read(row) == []


def _read(row):
    def handle(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'success': True, 'data': [row]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        transport = CloudbedsHTTPTransport(api_key='synthetic', property_id='synthetic', client=client)
        return transport._lodging({'check_in': date(2026, 10, 1).isoformat(), 'check_out': date(2026, 10, 4).isoformat(), 'adults': 2, 'children': 0})['options']



def test_real_boundary_membership_uses_authenticated_receipt_and_rejects_missing_row():
    from tests.test_phase8_boundary_atomic_commit import Phase8BoundaryAtomicCommitTests
    from reservation_boundary.worker_store import SQLiteBoundaryWorkerStore
    from v2_application.relay_worker import _complete_relay_group

    fixture = Phase8BoundaryAtomicCommitTests()
    fixture.setUp()
    try:
        from dataclasses import replace
        from reservation_boundary.sqlite_store import CommandRelayWrite
        current, token, commit, receipt, artifacts, _, internal, public = fixture._case()
        bundle = build_reservation_relay_bundle(commit.commands[0])
        relays = (CommandRelayWrite('relay-1', commit.commands[0].command_id, bundle.to_canonical_bytes(), bundle.artifact_hash),)
        receipt = replace(receipt, relay_rows=(('relay-1', bundle.artifact_hash),), artifact_hash='')
        fixture.store.commit_turn_v8(
            expected_version=current.version, fencing_token=token, commit=commit,
            receipt=receipt, artifacts=artifacts, command_relays=relays,
            internal_jobs=internal, public_rows=public, committed_at=receipt.committed_at,
        )
        source = SQLiteBoundaryWorkerStore(fixture.store)
        claim = source.claim_command_relay(worker_id='worker:commercial', now=NOW, lease_ttl=timedelta(seconds=30))
        proof, bundles = _complete_relay_group(source, claim)
        assert proof.artifact_hash == claim.source_turn_receipt_hash
        assert len(bundles) == 1
        assert bundles[0].artifact_hash == claim.bundle_hash
        fixture.store._connection.execute('DELETE FROM boundary_command_relays WHERE relay_id=?', (claim.relay_id,))
        with pytest.raises(RuntimeError, match='absent'):
            _complete_relay_group(source, claim)
    finally:
        fixture.doCleanups()
